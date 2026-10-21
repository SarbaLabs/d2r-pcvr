#!/usr/bin/env python3
"""Route the purchased Steam game through Proton; never create a separate prefix."""
import json
import os
from pathlib import Path
import sys
import mod_identity

ROOT = Path(__file__).resolve().parent.parent
FLAGS = {'--pcvr-steamvr': 'steamvr', '--pcvr-wivrn': 'wivrn'}


def runtime_manifest(path, mode):
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text())
    library = data['runtime']['library_path']
    target = Path(library)
    if not target.is_absolute():
        target = path.parent / target
    if not target.is_file():
        raise ValueError(f'Runtime library missing: {target}')
    identity = (str(path) + ' ' + str(target)).lower()
    markers = ('steamxr', 'steamvr') if mode == 'steamvr' else ('wivrn', 'monado')
    if not any(marker in identity for marker in markers):
        raise ValueError(f'Manifest does not identify the selected {mode} runtime: {path}')
    return str(path)



def route(arguments, config, environment):
    command, env = list(arguments), dict(environment)
    modes = [FLAGS[arg] for arg in command if arg in FLAGS]
    if not modes:
        return command, env
    if len(modes) != 1:
        raise ValueError('Select exactly one VR runtime.')
    mode = modes[0]
    command = [arg for arg in command if arg not in FLAGS]
    matches = [i for i, arg in enumerate(command) if Path(arg).name == 'D2R.exe']
    if len(matches) != 1 or not environment.get('STEAM_COMPAT_DATA_PATH'):
        raise ValueError('Launch the purchased Steam game using its configured Proton version.')
    index = matches[0]
    game = Path(command[index]).resolve().parent
    if game != Path(config['game']).resolve():
        raise ValueError('Steam launched a different game than configured.')
    if game.parent.name != 'common' or game.parent.parent.name != 'steamapps':
        raise ValueError('The game must be in a native Steam library.')
    if not (game / 'D2RLoader.exe').is_file():
        raise ValueError('D2RLoader is missing. Run setup to repair the installation.')
    if '-mod' in command:
        raise ValueError('Remove the conflicting -mod option before using this VR shortcut.')
    manifest = runtime_manifest(config[mode + '_runtime'], mode)
    if mode == 'wivrn':
        # Steam imports the active host runtime into its container. A /usr
        # override inside that container can point to a different library.
        active = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'openxr/1/active_runtime.json'
        if not active.is_file() or runtime_manifest(active, mode) != manifest:
            raise ValueError('Connect WiVRn and make it the active OpenXR runtime before launching.')
        env.pop('XR_RUNTIME_JSON', None)
    else:
        env['XR_RUNTIME_JSON'] = manifest
    env.pop('PROTON_ENABLE_WAYLAND', None)
    env['PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES'] = '1'
    env['STEAM_COMPAT_INSTALL_PATH'] = str(game)
    library = str(game.parent.parent)
    env['STEAM_COMPAT_LIBRARY_PATHS'] = library + (':' + env['STEAM_COMPAT_LIBRARY_PATHS'] if env.get('STEAM_COMPAT_LIBRARY_PATHS') else '')
    env['PROTON_LOG'] = '1'
    env['PROTON_LOG_DIR'] = str(game / 'd2rloader/logs')
    env['WINEDEBUG'] = '-all,+timestamp,+pid,+tid,err+all'
    command[index] = str(game / 'D2RLoader.exe')
    command += ['-mod', mod_identity.MOD_NAME]
    return command, env


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    config = json.loads((ROOT / 'linux-launch.json').read_text()) if any(arg in FLAGS for arg in arguments) else {}
    command, env = route(arguments, config, os.environ)
    if any(arg in FLAGS for arg in arguments):
        Path(env['PROTON_LOG_DIR']).mkdir(parents=True, exist_ok=True)
    os.execvpe(command[0], command, env)
