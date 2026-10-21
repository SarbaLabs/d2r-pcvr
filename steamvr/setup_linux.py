"""Linux Qt setup backend, sharing upstream game installation and backup logic."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid
import zlib

import install
import mod_identity
import steam_config as vdf
import uninstall_windows as removal
import linux_artwork
from linux_artwork import NAMES
from linux_preferences import LANGUAGES, preferences, preference_updates
from launch_linux import runtime_manifest
from install import require_game_closed

APP = 'D2R-PCVR-Linux'
STATE = 'linux-managed.json'

def steam_profiles(steam):
    return sorted(p.parent.parent for p in (Path(steam) / 'userdata').glob('*/config/localconfig.vdf')
                  if p.parent.parent.name.isdecimal())


def discover(steam=None):
    home = Path.home()
    roots = [Path(steam)] if steam else [home / '.local/share/Steam', home / '.steam/steam', home / '.steam/root']
    root = next((p.resolve() for p in roots if (p / 'steamapps').is_dir()), roots[0])
    libraries = [root]
    source = root / 'steamapps/libraryfolders.vdf'
    if source.is_file():
        tree = vdf.text_value(vdf.parse_text(source.read_text()), 'libraryfolders') or []
        for item in tree:
            if isinstance(item.value, list):
                path = vdf.text_value(item.value, 'path')
                if path:
                    libraries.append(Path(path))
    games, runtimes = [], []
    for library in dict.fromkeys(libraries):
        manifest = library / 'steamapps/appmanifest_2536520.acf'
        folder = 'Diablo II Resurrected'
        if manifest.is_file():
            folder = vdf.text_value(vdf.parse_text(manifest.read_text()), 'AppState', 'installdir') or folder
        game = library / 'steamapps/common' / folder
        if (game / 'D2R.exe').is_file():
            games.append(str(game))
        runtime = library / 'steamapps/common/SteamVR/steamxr_linux64.json'
        if runtime.is_file():
            runtimes.append(str(runtime))
    return {'steam_root': str(root), 'steam': shutil.which('steam') or '/usr/bin/steam',
            'games': games, 'steamvr_runtime': next(iter(runtimes), ''),
            'wivrn_runtime': next((str(p) for p in [Path('/usr/share/openxr/1/openxr_wivrn.json'),
                home / '.config/openxr/1/active_runtime.json'] if p.is_file()), '')}


def default_saves(game):
    return Path(game).parent.parent / 'compatdata/2536520/pfx/drive_c/users/steamuser/Saved Games/Diablo II Resurrected'


def require_closed():
    require_game_closed()
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            if path.read_text().strip().lower() in ('steam', 'steamwebhelper'):
                raise ValueError('Exit Steam completely (Steam > Exit), then try again. Setup never closes it for you.')
        except (FileNotFoundError, PermissionError):
            continue


def current_options(profile):
    text = (Path(profile) / 'config/localconfig.vdf').read_text(encoding='utf-8-sig')
    return vdf.text_value(vdf.parse_text(text), 'UserLocalConfigStore', 'Software', 'Valve',
                          'Steam', 'apps', vdf.APP_ID, 'LaunchOptions') or ''




def shortcuts(original, steam, game, modes):
    root = vdf.decode_binary(original) if original is not None else {'shortcuts': {}}
    if original is not None and vdf.encode_binary(root) != original:
        raise ValueError('Steam shortcuts cannot be safely round-tripped.')
    entries = root.get('shortcuts')
    if not isinstance(entries, dict) or any(not k.isdecimal() or not isinstance(v, dict) for k, v in entries.items()):
        raise ValueError('Unexpected Steam shortcut structure.')
    # Only own named entries for this game are updated; unrelated entries survive.
    for mode in modes:
        name = NAMES[mode]
        matches = [k for k, v in entries.items() if v.get('appname', v.get('AppName')) == name]
        if len(matches) > 1:
            raise ValueError('Remove duplicate Steam shortcuts named ' + name)
        if matches and not linux_artwork.belongs(entries[matches[0]], game):
            raise ValueError('A shortcut with the same name belongs to another installation: ' + name)
        key = matches[0] if matches else str(max(map(int, entries), default=-1) + 1)
        exe = '"' + str(steam) + '"'
        item = entries.setdefault(key, {})
        item.pop('AppName', None)
        appid = item.get('appid', zlib.crc32((exe + name).encode()) | 0x80000000)
        if any(k != key and v.get('appid') == appid for k, v in entries.items()):
            raise ValueError('Steam shortcut ID collision; no files were changed.')
        item.update({'appid': appid, 'appname': name,
                     'Exe': exe, 'StartDir': '"' + str(Path(steam).parent) + '"',
                     'icon': str(Path(game) / 'D2R.exe'), 'D2RPCVRLinuxGame': str(Path(game).resolve()), 'ShortcutPath': '',
                     'LaunchOptions': '-applaunch 2536520 --pcvr-' + mode,
                     'IsHidden': 0, 'AllowDesktopConfig': 0, 'AllowOverlay': 0,
                     'OpenVR': int(mode == 'steamvr'), 'Devkit': 0, 'DevkitGameID': '',
                     'DevkitOverrideAppID': 0, 'FlatpakAppID': ''})
        item.setdefault('LastPlayTime', 0)
        item.setdefault('tags', {'0': 'D2R VR Unleashed'})
    return vdf.encode_binary(root)



def validate(values):
    c = copy.deepcopy(values)
    for key in (('game',) if c.get('action') == 'uninstall' else ('game', 'steam_root', 'profile', 'saves')):
        if not c.get(key):
            raise ValueError('Choose the ' + key.replace('_', ' ') + ' folder.')
        c[key] = str(Path(c[key]).expanduser().resolve())
    game = Path(c['game'])
    if game.parent.name != 'common' or game.parent.parent.name != 'steamapps':
        raise ValueError('Select D2R in a native Steam library (Flatpak Steam is not supported).')
    c['destination'] = str(game / APP)
    destination = Path(c['destination'])
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError('The managed installation folder must be a real directory.')
    if destination.exists() and any(destination.iterdir()) and not (destination / STATE).is_file():
        raise ValueError('The managed installation folder is occupied by unrecognized files.')
    if c.get('action') == 'uninstall':
        state = read_state(destination, game)
        if not state:
            raise ValueError('No managed Linux installation was found.')
        return c
    install.validate_game(game, 'steam', False)
    c['steam'] = str(Path(c['steam']).expanduser().absolute())
    if not Path(c['steam']).is_file() or not os.access(c['steam'], os.X_OK):
        raise ValueError('Choose the native Steam executable, usually /usr/bin/steam.')
    if Path(c['profile']) not in steam_profiles(c['steam_root']):
        raise ValueError('Choose an existing account in the selected Steam data folder.')
    if not (Path(c['saves']) / 'Settings.json').is_file():
        raise ValueError('Launch unmodded D2R once first, or choose its existing Saved Games folder.')
    if not c.get('modes') or not set(c['modes']) <= NAMES.keys():
        raise ValueError('Select SteamVR, WiVRn, or both.')
    if any(c.get(key) not in LANGUAGES for key in ('text_locale', 'audio_locale')):
        raise ValueError('Choose a supported text and audio language.')
    if c.get('action') not in ('install', 'repair', 'shortcuts'):
        raise ValueError('Unknown setup action.')
    if c['action'] == 'shortcuts' and not (game / mod_identity.PLUGIN).is_file():
        raise ValueError('Install the mod before configuring shortcuts only.')
    for mode in c['modes']:
        c[mode + '_runtime'] = runtime_manifest(c[mode + '_runtime'], mode)
    return c


def read_state(destination, game):
    path = destination / STATE
    if not path.is_file():
        return {}
    state = json.loads(path.read_text())
    if state.get('game') != str(game.resolve()):
        raise ValueError('The setup record belongs to a different game.')
    return state


def contents(path):
    if path.is_symlink():
        raise ValueError('Refusing to replace a symbolic link: ' + str(path))
    return path.read_bytes() if path.exists() else None


def pack(data):
    return None if data is None else base64.b64encode(data).decode('ascii')


def unpack(data):
    return None if data is None else base64.b64decode(data, validate=True)


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def track_updates(state, updates, originals=None):
    tracked = state.setdefault('files', {})
    for path, data in updates.items():
        old = originals[path] if originals and path in originals else contents(path)
        previous = tracked.get(str(path))
        # A user replacement becomes the new baseline, not something to discard.
        baseline = previous['original'] if previous and digest(old) == previous['installed'] else pack(old)
        tracked[str(path)] = {'original': baseline, 'installed': digest(data)}


def uninstall_choices(c):
    game, destination = Path(c['game']), Path(c['destination'])
    state = read_state(destination, game)
    updates = {}
    record = install.read_record(game)
    if record:
        chain = removal.snapshots(game, record, [destination / 'game-backups'])
        updates.update(removal.restore_plan(game, chain, restore_video=False))
    for name, info in state.get('files', {}).items():
        path = Path(name)
        if digest(contents(path)) == info['installed']:
            updates[path] = unpack(info['original'])
        else:
            updates.pop(path, None)  # Preserve settings/artwork changed by the player.
    for name, profile_state in state.get('profiles', {}).items():
        profile = Path(name)
        local = profile / 'config/localconfig.vdf'
        if local.exists() and current_options(profile) == profile_state['installed_options']:
            updates[local] = vdf.set_launch_options(local.read_text(), profile_state['original_options']).encode()
        shortcut_file = profile / 'config/shortcuts.vdf'
        if shortcut_file.is_file():
            payload = shortcut_file.read_bytes()
            tree = vdf.decode_binary(payload)
            if vdf.encode_binary(tree) != payload:
                raise ValueError('Cannot safely read Steam shortcuts.')
            tree['shortcuts'] = {k: entry for k, entry in tree['shortcuts'].items()
                if not any(all(entry.get(key) == owned.get(key) for key in
                    ('appid', 'appname', 'Exe', 'LaunchOptions', 'D2RPCVRLinuxGame'))
                    for owned in profile_state['entries'])}
            updates[shortcut_file] = vdf.encode_binary(tree)
    # Shared IDs mean another shortcut may still use its artwork.
    for name in state.get('profiles', {}):
        file = Path(name) / 'config/shortcuts.vdf'
        payload = updates.get(file, contents(file))
        ids = {str(e.get('appid', 0) & 0xffffffff) for e in vdf.decode_binary(payload or vdf.encode_binary({'shortcuts': {}}))['shortcuts'].values()}
        for path in list(updates):
            if path.parent == Path(name) / 'config/grid' and path.name.split('_')[0].split('.')[0].removesuffix('p') in ids:
                del updates[path]
    updates[destination / STATE] = json.dumps({'game': str(game), 'removed': True}).encode()
    removal.apply_updates(updates, destination / 'configuration-backups', require_closed)
    print('Uninstalled. Characters and current video settings were kept. Recovery backups and setup remain in ' + str(destination), flush=True)


def install_choices(choices, source_bundle, payload, runner):
    c = validate(choices)
    require_closed()
    if c['action'] == 'uninstall':
        return uninstall_choices(c)
    game, profile, destination = Path(c['game']), Path(c['profile']), Path(c['destination'])
    if current_options(profile) != c['reviewed_options']:
        raise ValueError('Steam launch options changed after review; review them again.')
    source_bundle, payload, runner = map(lambda p: Path(p).resolve(), (source_bundle, payload, runner))
    runtime = destination / 'runtime'
    installed_payload = runtime / payload.relative_to(source_bundle)
    installed_runner = runtime / runner.relative_to(source_bundle)
    options = shlex.quote(str(installed_runner)) + ' --route %command%'
    local, shortcut_file = profile / 'config/localconfig.vdf', profile / 'config/shortcuts.vdf'
    before_local, before_shortcuts = contents(local), contents(shortcut_file)
    planned = shortcuts(before_shortcuts, c['steam'], game, c['modes'])
    planned, artwork = linux_artwork.steam_updates(planned, profile, game, c['modes'], payload / 'artwork')
    desktop = linux_artwork.desktop_updates(c['steam'], game, c['modes'], payload / 'artwork')
    # Point desktop icons at the permanent bundle, not the extracted download.
    desktop = {p: data.replace(linux_artwork.desktop_string(payload).encode(), linux_artwork.desktop_string(installed_payload).encode()) for p, data in desktop.items()}
    state = read_state(destination, game)
    if state.get('removed'):
        state = {}
    state['game'] = str(game)
    profile_state = state.setdefault('profiles', {}).setdefault(str(profile), {'original_options': c['reviewed_options'], 'entries': []})
    if profile_state.get('installed_options', c['reviewed_options']) != c['reviewed_options']:
        profile_state['original_options'] = c['reviewed_options']
    profile_state['installed_options'] = options
    profile_state['entries'] = [entry for entry in vdf.decode_binary(planned)['shortcuts'].values() if linux_artwork.belongs(entry, game)]
    # Check all image/desktop targets before touching the game.
    track_updates(state, {**artwork, **desktop})
    destination.mkdir(parents=True, exist_ok=True)
    backups = destination / 'game-backups'
    before_snapshots = set(backups.glob('*/manifest.json'))
    before_record = contents(game / install.RECORD)
    language_path = game / 'd2rloader/config/d2rloader.toml'
    before_language = contents(language_path)
    if not (destination / STATE).exists():
        (destination / STATE).write_text(json.dumps({'game': str(game)}))
    stage = Path(tempfile.mkdtemp(prefix='runtime-stage-', dir=destination))
    old = destination / ('runtime-previous-' + uuid.uuid4().hex)
    promoted = False
    try:
        shutil.copytree(source_bundle, stage, dirs_exist_ok=True)
        previous_config = installed_payload / 'linux-launch.json'
        config = json.loads(previous_config.read_text()) if previous_config.exists() else {}
        config.update({key: c[key] for key in ('game', 'steamvr_runtime', 'wivrn_runtime') if c.get(key)})
        (stage / payload.relative_to(source_bundle) / 'linux-launch.json').write_text(json.dumps(config))
        require_closed()
        if runtime.exists():
            runtime.rename(old)
        stage.rename(runtime)
        promoted = True
        if c['action'] != 'shortcuts':
            command = [str(installed_runner), '--install-game', '--game', str(game), '--store', 'steam',
                       '--saves', c['saves'], '--plugin', str(installed_payload / 'bin/d2rl-pcvr.dll'),
                       '--openxr-loader', str(installed_payload / 'bin/openxr_loader.dll'),
                       '--deps', str(destination / 'deps'), '--backup-dir', str(backups), '--download-deps']
            if c['action'] == 'repair':
                command.append('--preserve-settings')
            subprocess.run(command, check=True)
        updates = {**artwork, **desktop}
        if c['action'] != 'shortcuts':
            language_updates = preference_updates(game, c)
            track_updates(state, language_updates, {language_path: before_language})
            updates.update(language_updates)
        if contents(local) != before_local or contents(shortcut_file) != before_shortcuts:
            raise ValueError('Steam configuration changed during setup. Close Steam and retry.')
        updates[local] = vdf.set_launch_options(before_local.decode('utf-8-sig'), options).encode()
        updates[shortcut_file] = planned
        updates[destination / STATE] = json.dumps(state, indent=2).encode()
        removal.apply_updates(updates, destination / 'configuration-backups', require_closed)
        print('Setup complete. Start Steam and use your SteamVR or WiVRn shortcut. The original Steam Play entry stays unmodded.', flush=True)
    except BaseException:
        print('Setup failed; restoring changes from this attempt...', flush=True)
        recovery = {}
        for manifest_path in sorted(set(backups.glob('*/manifest.json')) - before_snapshots, reverse=True):
            manifest = json.loads(manifest_path.read_text())
            recovery.update(removal.restore_plan(game, [(manifest_path.parent, manifest)], restore_video=True))
        recovery[game / install.RECORD] = before_record
        removal.apply_updates(recovery, destination / 'configuration-backups', require_closed)
        if promoted:
            runtime.rename(destination / ('runtime-failed-' + uuid.uuid4().hex))
        if old.exists():
            old.rename(runtime)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if old.exists():
        shutil.rmtree(old)
