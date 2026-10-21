"""Temporary fixtures only: Linux routing, Steam ownership, install rollback and removal."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import setup_linux as setup
import linux_artwork as art
import linux_preferences as prefs
import launch_linux as launch
import steam_config as vdf
import install
import mod_identity
import install_test

LOCAL = '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "2536520" { "LaunchOptions" "-old" } "42" { "LaunchOptions" "untouched" } } } } } }'
ROOT = Path(__file__).resolve().parent.parent


class LinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = install_test.RenameMigrationTests()
        game, self.saves = self.fixture.fixture(self.root)
        self.game = self.root / 'library/steamapps/common/D2R'
        self.game.parent.mkdir(parents=True)
        game.rename(self.game)
        self.steam = self.root / 'Steam'
        self.profile = self.steam / 'userdata/123'
        self.local = self.profile / 'config/localconfig.vdf'
        self.local.parent.mkdir(parents=True)
        self.local.write_text(LOCAL)
        self.bundle = self.root / 'download'
        self.payload = self.bundle / '_internal/payload'
        self.payload.mkdir(parents=True)
        shutil.copytree(ROOT / 'artwork', self.payload / 'artwork')
        shutil.copytree(self.root / 'bin', self.payload / 'bin')
        self.runner = self.bundle / 'Setup'
        self.runner.write_text('fixture')
        self.runtime = self.root / 'steamvr.json'
        self.library = self.root / 'steamvr.so'
        self.library.write_text('fixture')
        self.runtime.write_text(json.dumps({'runtime': {'library_path': str(self.library)}}))
        self.c = dict(game=str(self.game), saves=str(self.saves), steam_root=str(self.steam),
                      steam='/bin/true', profile=str(self.profile), modes=['steamvr'],
                      steamvr_runtime=str(self.runtime), wivrn_runtime='', action='repair',
                      text_locale='frFR', audio_locale='jaJP', reviewed_options='-old')
        self.dest = self.game / setup.APP
        self.addCleanup(patch.stopall)
        patch.object(setup, 'require_closed').start()
        patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root / 'xdg')}).start()

    def run_setup(self, failure=None):
        def worker(command, check):
            # Run the actual upstream installer with harmless local dependencies.
            deps = self.dest / 'deps'
            shutil.copytree(self.root / 'deps', deps, dirs_exist_ok=True)
            self.fixture.argv = ['install.py'] + command[2:]
            self.fixture.argv.remove('--download-deps')
            self.fixture.run_install()
            if failure:
                raise OSError(failure)
        self.c['reviewed_options'] = setup.current_options(self.profile)
        with patch.object(setup.subprocess, 'run', side_effect=worker):
            setup.install_choices(self.c, self.bundle, self.payload, self.runner)

    def test_install_repair_uninstall_preserves_progress_and_other_apps(self):
        self.run_setup()
        first = (self.profile / 'config/shortcuts.vdf').read_bytes()
        settings = self.game / mod_identity.SETTINGS
        calibration = b'[body_calibration]\nheight=1.69\n'
        settings.write_bytes(calibration)
        self.run_setup()
        self.assertEqual(first, (self.profile / 'config/shortcuts.vdf').read_bytes())
        self.assertEqual(settings.read_bytes(), calibration)
        self.assertEqual(prefs.preferences(self.game), {'text_locale': 'frFR', 'audio_locale': 'jaJP'})
        (self.saves / 'Friend.d2s').write_bytes(b'new progress')
        grid = self.profile / 'config/grid'
        self.assertEqual(len(list(grid.glob('*.png'))), 5)
        custom = next(grid.glob('*p.png'))
        custom.write_bytes(b'player portrait')
        shortcut_file = self.profile / 'config/shortcuts.vdf'
        played = vdf.decode_binary(shortcut_file.read_bytes())
        played['shortcuts']['0']['LastPlayTime'] = 123456
        shortcut_file.write_bytes(vdf.encode_binary(played))
        setup.uninstall_choices(dict(self.c, destination=str(self.dest)))
        self.assertFalse((self.game / mod_identity.PLUGIN).exists())
        self.assertEqual((self.saves / 'Friend.d2s').read_bytes(), b'new progress')
        self.assertEqual(custom.read_bytes(), b'player portrait')
        self.assertEqual(setup.current_options(self.profile), '-old')
        self.assertIn('untouched', self.local.read_text())
        self.assertFalse((self.game / 'd2rloader/config/d2rloader.toml').exists())
        self.assertEqual(vdf.decode_binary((self.profile / 'config/shortcuts.vdf').read_bytes())['shortcuts'], {})

    def test_failed_install_rolls_back_game_and_keeps_previous_launcher(self):
        self.run_setup()
        before = (self.game / mod_identity.PLUGIN).read_bytes()
        old_launcher = (self.dest / 'runtime/Setup').read_bytes()
        self.runner.write_bytes(b'new launcher')
        with self.assertRaisesRegex(OSError, 'fixture failure'):
            self.run_setup('fixture failure')
        self.assertEqual((self.game / mod_identity.PLUGIN).read_bytes(), before)
        self.assertEqual((self.dest / 'runtime/Setup').read_bytes(), old_launcher)

    def test_shortcuts_only_does_not_run_game_installer_or_change_languages(self):
        self.run_setup()
        self.c.update(action='shortcuts', text_locale='enUS')
        self.c['reviewed_options'] = setup.current_options(self.profile)
        with patch.object(setup.subprocess, 'run') as worker:
            setup.install_choices(self.c, self.bundle, self.payload, self.runner)
            worker.assert_not_called()
        self.assertEqual(prefs.preferences(self.game)['text_locale'], 'frFR')

    def test_unrelated_name_and_id_collision_are_refused(self):
        payload = setup.shortcuts(None, '/bin/true', self.game, ['steamvr'])
        tree = vdf.decode_binary(payload)
        tree['shortcuts']['0']['D2RPCVRLinuxGame'] = '/other'
        with self.assertRaisesRegex(ValueError, 'another installation'):
            setup.shortcuts(vdf.encode_binary(tree), '/bin/true', self.game, ['steamvr'])
        tree['shortcuts']['0']['appname'] = 'Other game'
        with self.assertRaisesRegex(ValueError, 'collision'):
            setup.shortcuts(vdf.encode_binary(tree), '/bin/true', self.game, ['steamvr'])

    def test_artwork_has_all_five_slots_and_wivrn_discovery_category(self):
        payload = setup.shortcuts(None, '/bin/true', self.game, ['steamvr', 'wivrn'])
        _, updates = art.steam_updates(payload, self.profile, self.game, list(art.NAMES), ROOT / 'artwork')
        self.assertEqual(len(updates), 10)
        desktop = art.desktop_updates('/path with spaces/steam', self.game, ['wivrn'], ROOT / 'artwork')
        content = next(iter(desktop.values())).decode()
        self.assertIn('Categories=Game;X-WiVRn-VR;', content)
        self.assertIn('Exec="/path with spaces/steam" -applaunch 2536520 --pcvr-wivrn', content)

    def test_language_root_keys_preserve_tables_and_reject_duplicates(self):
        path = self.game / 'd2rloader/config/d2rloader.toml'
        path.parent.mkdir(parents=True)
        path.write_text('text_locale = "enUS"\n[other]\ntext_locale = "keep"\n')
        content = prefs.preference_updates(self.game, self.c)[path].decode()
        self.assertIn('text_locale = "frFR"', content)
        self.assertIn('[other]\ntext_locale = "keep"', content)
        path.write_text('text_locale="enUS"\ntext_locale="deDE"\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            prefs.preference_updates(self.game, self.c)

    def test_stock_route_is_unchanged_and_vr_keeps_proton_prefix(self):
        args = ['proton', 'run', str(self.game / 'D2R.exe')]
        env = {'STEAM_COMPAT_DATA_PATH': '/existing/prefix', 'PROTON_ENABLE_WAYLAND': '1'}
        self.assertEqual(launch.route(args, {}, env), (args, env))
        (self.game / 'D2RLoader.exe').write_text('fixture')
        cmd, result = launch.route(args + ['--pcvr-steamvr'], self.c, env)
        self.assertIn(str(self.game / 'D2RLoader.exe'), cmd)
        self.assertEqual(cmd[-2:], ['-mod', 'D2R-PCVR'])
        self.assertEqual(result['STEAM_COMPAT_DATA_PATH'], '/existing/prefix')
        self.assertNotIn('PROTON_ENABLE_WAYLAND', result)
        self.assertEqual(result['XR_RUNTIME_JSON'], str(self.runtime))

    def test_wivrn_requires_active_runtime_and_clears_host_override(self):
        library = self.root / 'libwivrn.so'
        library.write_text('fixture')
        manifest = self.root / 'wivrn.json'
        manifest.write_text(json.dumps({'runtime': {'library_path': str(library)}}))
        config_home = self.root / 'config'
        active = config_home / 'openxr/1/active_runtime.json'
        active.parent.mkdir(parents=True)
        (self.game / 'D2RLoader.exe').write_text('fixture')
        args = ['proton', 'run', str(self.game / 'D2R.exe'), '--pcvr-wivrn']
        env = {'STEAM_COMPAT_DATA_PATH': '/prefix', 'XR_RUNTIME_JSON': '/host/old.json'}
        config = dict(self.c, wivrn_runtime=str(manifest))
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}):
            with self.assertRaisesRegex(ValueError, 'Connect WiVRn'):
                launch.route(args, config, env)
            active.symlink_to(manifest)
            _, result = launch.route(args, config, env)
            self.assertNotIn('XR_RUNTIME_JSON', result)
            self.assertEqual(result['PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES'], '1')

    def test_changed_launch_options_stop_before_install(self):
        self.c['reviewed_options'] = 'different'
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            setup.install_choices(self.c, self.bundle, self.payload, self.runner)
        self.assertFalse(self.dest.exists())

    def test_failed_configuration_commit_restores_all_files(self):
        a, b = self.root / 'a', self.root / 'b'
        a.write_bytes(b'original a')
        b.write_bytes(b'original b')
        real = os.replace
        def fail_second(source, target):
            if target == b:
                raise OSError('copy failure')
            return real(source, target)
        with patch.object(setup.removal.os, 'replace', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'copy failure'):
                setup.removal.apply_updates({a: b'new a', b: b'new b'}, self.root / 'backups', lambda: None)
        self.assertEqual(a.read_bytes(), b'original a')
        self.assertEqual(b.read_bytes(), b'original b')


if __name__ == '__main__':
    unittest.main()
