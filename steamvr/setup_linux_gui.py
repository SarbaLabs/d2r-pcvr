"""English Qt Widgets installer. All writes run in a separate logged worker."""
import json
import os
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (QApplication, QWizard, QWizardPage, QVBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QHBoxLayout, QFileDialog, QComboBox, QCheckBox,
    QPlainTextEdit, QMessageBox)
import setup_linux as backend


class Setup(QWizard):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.setWindowTitle('D2R VR Unleashed — Linux Setup (Personal test)')
        self.resize(850, 680)
        self.setOption(QWizard.NoBackButtonOnStartPage)
        self.setOption(QWizard.NoBackButtonOnLastPage)
        self.fields = {}
        self.process = None
        self.choice_file = None
        self.pending = None
        self.completed = False
        self._preferences_game = None
        self.build_pages()
        self.currentIdChanged.connect(self.enter_page)
        try:
            self.detect()
        except (OSError, ValueError) as error:
            self.status.setText('Automatic detection unavailable: ' + str(error))

    def page(self, title, subtitle):
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        self.addPage(page)
        return page

    def path_row(self, form, key, label, directory=True):
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setAccessibleName(label)
        self.fields[key] = edit
        row.addWidget(edit)
        button = QPushButton('Browse…')
        def browse():
            value = (QFileDialog.getExistingDirectory(self, label, edit.text()) if directory else
                     QFileDialog.getOpenFileName(self, label, edit.text())[0])
            if value:
                edit.setText(value)
                if key == 'game':
                    self.load_game()
                elif key == 'steam_root':
                    self.refresh_profiles()
        button.clicked.connect(browse)
        row.addWidget(button)
        form.addRow(label, row)
        return edit

    def build_pages(self):
        page = self.page('Welcome', 'Install D2R VR Unleashed into your existing Steam game.')
        layout = QVBoxLayout(page)
        label = QLabel('This preview supports the Steam edition on Linux x86-64 with native Steam.\n\n'
            'Launch the unmodded game once before setup. Your existing Proton prefix,\n'
            'characters are preserved; repair also keeps VR calibration. No game files are distributed.\n\n'
            'Setup downloads verified D2RLoader, ReShade and shader compiler dependencies.\n'
            'Use local offline characters. Battle.net and Flatpak Steam are not supported yet.\n\n'
            'Close the game and exit Steam before installing. Setup does not close them for you.')
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch()
        page = self.page('Folders and Steam account', 'Detected paths are suggestions. You can use another Steam library or disk.')
        form = QFormLayout(page)
        self.path_row(form, 'steam_root', 'Steam data folder').editingFinished.connect(self.refresh_profiles)
        self.path_row(form, 'steam', 'Steam executable', False)
        self.path_row(form, 'game', 'Diablo II: Resurrected folder').editingFinished.connect(self.load_game)
        self.path_row(form, 'saves', 'Existing Saved Games folder')
        location = QLabel('Setup and launcher are installed automatically in the game folder, under D2R-PCVR-Linux.')
        location.setWordWrap(True)
        form.addRow(location)
        self.accounts = QComboBox()
        form.addRow('Steam account (userdata ID)', self.accounts)
        button = QPushButton('Detect folders again')
        button.clicked.connect(self.safe_detect)
        form.addRow(button)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        form.addRow(self.status)
        page = self.page('Launch modes', 'Select SteamVR, WiVRn, or both. Each gets its own Steam and application-menu shortcut.')
        form = QFormLayout(page)
        self.headset = QComboBox()
        self.headset.addItems(['Steam Frame', 'Quest 3', 'Quest 2', 'Other OpenXR headset'])
        form.addRow('Headset', self.headset)
        self.mode_boxes = {}
        for mode, label in [('steamvr', 'Steam Link / SteamVR'), ('wivrn', 'WiVRn')]:
            box = QCheckBox(label)
            box.setChecked(True)
            self.mode_boxes[mode] = box
            form.addRow(box)
        self.headset.currentIndexChanged.connect(lambda index: [box.setChecked(mode == 'steamvr' or index in (1, 2)) for mode, box in self.mode_boxes.items()])
        all_button = QPushButton('Both (create two Steam library shortcuts)')
        all_button.clicked.connect(lambda: [box.setChecked(True) for box in self.mode_boxes.values()])
        form.addRow(all_button)
        self.path_row(form, 'steamvr_runtime', 'SteamVR Linux OpenXR manifest', False)
        self.path_row(form, 'wivrn_runtime', 'WiVRn Linux OpenXR manifest', False)
        note = QLabel('VR uses XWayland. Keep HDR disabled for the initial test.\n'
                      'Connect the headset through the selected server before playing.\n'
                      'WiVRn must select its system runtime; setup does not change the global runtime.')
        note.setWordWrap(True)
        form.addRow(note)
        page = self.page('Installation and languages', 'Existing preferences are loaded when you select the game folder.')
        form = QFormLayout(page)
        self.action = QComboBox()
        for name, value in [('Update / repair (keep calibration and video settings)', 'repair'),
                            ('Full install (recommended settings; resets calibration)', 'install'),
                            ('Configure shortcuts only (keep the installed mod)', 'shortcuts'),
                            ('Uninstall (keep characters and current video settings)', 'uninstall')]:
            self.action.addItem(name, value)
        form.addRow('Action', self.action)
        self.language_boxes = {}
        for key, label in [('text_locale', 'Text language'), ('audio_locale', 'Audio language')]:
            combo = QComboBox()
            for code, name in backend.LANGUAGES.items():
                combo.addItem(name, code)
            self.language_boxes[key] = combo
            form.addRow(label, combo)
        self.action.currentIndexChanged.connect(lambda: [box.setEnabled(self.action.currentData() in ('install', 'repair')) for box in self.language_boxes.values()])
        form.addRow(QLabel('Default leaves the override empty and uses the game language.\nVR button bindings are preserved.'))
        page = self.page('Review installation', 'Review the Steam launch options before installing. Exit Steam completely now.')
        layout = QVBoxLayout(page)
        self.review = QPlainTextEdit()
        self.review.setReadOnly(True)
        layout.addWidget(self.review)
        self.accept_options = QCheckBox('Apply the reviewed action (including Steam launch-option changes)')
        layout.addWidget(self.accept_options)
        page.setCommitPage(True)
        self.setButtonText(QWizard.CommitButton, 'Install')
        page = self.page('Installation', 'Keep this window open until setup finishes.')
        layout = QVBoxLayout(page)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

    def safe_detect(self):
        try:
            self.detect(self.fields['steam_root'].text() or None)
        except (ValueError, OSError, KeyError) as error:
            QMessageBox.warning(self, 'Detection', str(error))

    def detect(self, steam=None):
        detected = backend.discover(steam)
        for key in ('steam_root', 'steam', 'steamvr_runtime', 'wivrn_runtime'):
            self.fields[key].setText(detected[key])
        if detected['games']:
            self.fields['game'].setText(detected['games'][0])
        self.refresh_profiles()
        self.load_game()
        self.status.setText('Select the account you use to own and launch D2R. Paths can be changed above.')

    def refresh_profiles(self):
        previous = self.accounts.currentData()
        self.accounts.clear()
        for profile in backend.steam_profiles(self.fields['steam_root'].text()):
            self.accounts.addItem(profile.name, str(profile))
        if previous:
            self.accounts.setCurrentIndex(self.accounts.findData(previous))
        elif self.accounts.count() > 1:
            self.accounts.setCurrentIndex(-1)

    def load_game(self):
        game = self.fields['game'].text()
        if game and game != self._preferences_game:
            self.fields['saves'].setText(str(backend.default_saves(game)))
            prefs = backend.preferences(game)
            for key, combo in self.language_boxes.items():
                combo.setCurrentIndex(combo.findData(prefs[key]))
            self._preferences_game = game

    def choices(self):
        values = {key: edit.text().strip() for key, edit in self.fields.items()}
        values.update(profile=self.accounts.currentData() or '',
                      modes=[key for key, box in self.mode_boxes.items() if box.isChecked()],
                      action=self.action.currentData())
        values.update({key: box.currentData() for key, box in self.language_boxes.items()})
        return values

    def enter_page(self, index):
        if index == 4:
            self.accept_options.setChecked(False)
            try:
                self.pending = backend.validate(self.choices())
                options = backend.current_options(self.pending['profile']) if self.pending['action'] != 'uninstall' else '(restore owned launch options and remove owned shortcuts)'
                self.pending['reviewed_options'] = options
                summary = '\n'.join([
                    'Game: ' + self.pending['game'],
                    'Steam account: ' + Path(self.pending['profile']).name,
                    'Install to: ' + self.pending['destination'],
                    'Modes: ' + ', '.join(backend.NAMES[m] for m in self.pending['modes']),
                    'Shortcuts: Steam library and application menu (WiVRn headset launcher included)',
                    'Action: ' + self.action.currentText(),
                    'Text language: ' + self.language_boxes['text_locale'].currentText(),
                    'Audio language: ' + self.language_boxes['audio_locale'].currentText(),
                    '', 'Current D2R launch options:', options or '(empty)'])
                self.review.setPlainText(summary + '\n\n'
                    'Old test wrappers and environment arguments above will be replaced.\n'
                    'Mode-specific settings are handled by the installed launcher.\n'
                    'Other Steam entries and the game\'s selected Proton version are preserved.\n'
                    'The upstream installer backs up saves; removal never restores character progress.')
            except (ValueError, OSError, KeyError) as error:
                self.pending = None
                self.review.setPlainText(str(error) + '\n\nGo Back to correct these settings.')
        elif index == 5:
            self.button(QWizard.FinishButton).setEnabled(False)
            self.button(QWizard.CancelButton).setEnabled(False)
            QTimer.singleShot(0, self.install)

    def validateCurrentPage(self):
        if self.currentId() == 1:
            self.refresh_profiles()
            self.load_game()
        if self.currentId() == 4:
            if not self.pending or not self.accept_options.isChecked():
                QMessageBox.warning(self, 'Review required', 'Correct the settings and accept the displayed launch-option replacement.')
                return False
            try:
                backend.require_closed()
            except (ValueError, SystemExit) as error:
                QMessageBox.warning(self, 'Close Steam and D2R', str(error))
                return False
        return super().validateCurrentPage()

    def install(self):
        if not getattr(sys, 'frozen', False):
            self.log.appendPlainText('Source GUI preview only. Build the portable package to install the embedded runtime.')
            self.button(QWizard.FinishButton).setEnabled(True)
            return
        handle = tempfile.NamedTemporaryFile(mode='w', prefix='pcvr-choices-', suffix='.json', delete=False)
        with handle:
            json.dump(self.pending, handle)
        self.choice_file = Path(handle.name)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished_install)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start(sys.executable, ['--install-choices', str(self.choice_file)])

    def process_error(self, error):
        self.log.appendPlainText(self.process.errorString())
        if error == QProcess.FailedToStart:
            self.finished_install(-1, QProcess.CrashExit)

    def read_output(self):
        self.log.appendPlainText(bytes(self.process.readAllStandardOutput()).decode(errors='replace').rstrip())

    def finished_install(self, code, status):
        self.read_output()
        self.completed = code == 0 and status == QProcess.NormalExit
        self.log.appendPlainText('\nSUCCESS — The selected setup action completed.' if self.completed else
            '\nSETUP FAILED — Review the log above. Keep this window or copy the log before closing.')
        self.choice_file.unlink(missing_ok=True)
        self.button(QWizard.FinishButton).setEnabled(True)
        self.button(QWizard.CancelButton).setEnabled(True)

    def reject(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            return
        super().reject()

    def closeEvent(self, event):
        if self.process and self.process.state() != QProcess.NotRunning:
            event.ignore()
        else:
            super().closeEvent(event)


def run(root, smoke=False):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('D2R VR Unleashed Setup')
    wizard = Setup(root)
    wizard.show()
    if smoke:
        def verify():
            assert wizard.pageIds() == list(range(6))
            assert wizard.language_boxes['text_locale'].count() == 14
            assert len(wizard.mode_boxes) == 2
            assert wizard.action.count() == 4
            wizard.grab().save(os.environ.get('PCVR_SETUP_SCREENSHOT', '/tmp/pcvr-setup.png'))
            print('Qt wizard smoke test: OK', flush=True)
            app.quit()
        QTimer.singleShot(150, verify)
    return app.exec()
