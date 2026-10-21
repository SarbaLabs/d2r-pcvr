"""Plan artwork and desktop entries; callers commit all files transactionally."""
import hashlib
import json
import os
from pathlib import Path

NAMES = {'steamvr': 'D2R VR Unleashed - SteamVR', 'wivrn': 'D2R VR Unleashed - WiVRn'}
FOLDERS = {'steamvr': 'SteamVR', 'wivrn': 'WiVRn'}
GRID = {'cover': 'p.png', 'wide-cover': '.png', 'background': '_hero.png', 'logo': '_logo.png'}


def belongs(entry, game):
    return entry.get('D2RPCVRLinuxGame') == str(Path(game).resolve())


def assets(directory, modes):
    directory = Path(directory)
    result = {}
    for mode in modes:
        result[mode] = {}
        for kind in ('icon', *GRID):
            rel = FOLDERS[mode] + '/' + {'icon': 'Icon', 'cover': 'Cover', 'wide-cover': 'Wide-Cover', 'background': 'Background', 'logo': 'Logo'}[kind] + '.png'
            path = directory / rel
            data = path.read_bytes()
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Expected original PNG artwork: ' + rel)
            result[mode][kind] = (path, data)
    return result


def steam_updates(payload, profile, game, modes, directory):
    import steam_config as vdf
    available = assets(directory, modes)
    tree = vdf.decode_binary(payload)
    updates = {}
    for mode in modes:
        matches = [e for e in tree['shortcuts'].values()
                   if e.get('appname') == NAMES[mode] and belongs(e, game)]
        if len(matches) != 1:
            raise ValueError('Cannot identify the artwork target: ' + NAMES[mode])
        entry = matches[0]
        icon = Path(profile) / 'config/grid' / (str(entry['appid'] & 0xffffffff) + '_icon.png')
        updates[icon] = available[mode]['icon'][1]
        entry['icon'] = str(icon)
        entry['D2RPCVRLinuxGame'] = str(Path(game).resolve())
        appid = entry['appid'] & 0xffffffff
        for kind, suffix in GRID.items():
            updates[Path(profile) / 'config/grid' / (str(appid) + suffix)] = available[mode][kind][1]
    return vdf.encode_binary(tree), updates


def desktop_string(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def desktop_exec(value):
    # Exec quoting is applied before the desktop-entry string escape layer.
    text = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        text = text.replace(char, '\\' + char)
    return desktop_string('"' + text + '"')


def desktop_updates(steam, game, modes, directory, applications=None):
    available = assets(directory, modes)
    if applications is None:
        home = os.environ.get('XDG_DATA_HOME', '')
        base = Path(home) if home and Path(home).is_absolute() else Path.home() / '.local/share'
        applications = base / 'applications'
    updates = {}
    for mode in modes:
        path = Path(applications) / ('d2r-pcvr-linux-' + mode + '.desktop')
        command = desktop_exec(steam) + ' -applaunch 2536520 --pcvr-' + mode
        if path.exists():
            text = path.read_text(encoding='utf-8')
            fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
            owned = fields.get('X-D2RPCVRLinux-Game') == desktop_string(Path(game).resolve())
            if not owned:
                raise ValueError('An unrelated desktop entry already exists: ' + str(path))
        text = '\n'.join(['[Desktop Entry]', 'Type=Application', 'Version=1.0',
            'Name=' + NAMES[mode], 'Exec=' + command,
            'Icon=' + desktop_string(available[mode]['icon'][0]), 'Terminal=false',
            'Categories=Game;' + ('X-WiVRn-VR;' if mode == 'wivrn' else ''),
            'X-D2RPCVRLinux-Game=' + desktop_string(Path(game).resolve()), ''])
        updates[path] = text.encode('utf-8')
    return updates
