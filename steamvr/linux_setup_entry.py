#!/usr/bin/env python3
"""Shared entry point for the portable Qt setup and the installed Steam wrapper."""
import json
import os
from pathlib import Path
import sys
import subprocess


def payload_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'payload'
    return Path(__file__).resolve().parent.parent


def clean_external_environment():
    if getattr(sys, 'frozen', False):
        original = os.environ.get('LD_LIBRARY_PATH_ORIG')
        if original is None:
            os.environ.pop('LD_LIBRARY_PATH', None)
        else:
            os.environ['LD_LIBRARY_PATH'] = original


def main():
    root = payload_root()
    args = sys.argv[1:]
    if args and args[0] == '--route':
        clean_external_environment()
        import launch_linux
        launch_linux.ROOT = root
        return launch_linux.main(args[1:])
    if args and args[0] == '--install-game':
        clean_external_environment()
        if '--download-deps' in args:
            import linux_tls
            linux_tls.configure()
        import install
        install.ROOT = root
        sys.argv = ['install.py'] + args[1:]
        install.main()
        return 0
    if args and args[0] == '--install-choices':
        import setup_linux
        setup_linux.install_choices(json.loads(Path(args[1]).read_text()), Path(sys.executable).parent,
                                    root, Path(sys.executable))
        return 0
    if args and args[0] == '--tls-check':
        import linux_tls
        linux_tls.check(args[1:] or [
            'https://d2rloader.net/downloads/D2RLoader-1.3.1-beta.zip',
            'https://reshade.me/downloads/ReShade_Setup_6.8.0_Addon.exe',
            'https://api.nuget.org/v3/index.json'])
        return 0
    if args and args[0] == '--self-test':
        # Imports exercise frozen dependencies without touching Steam/game files.
        import install
        import setup_linux
        import launch_linux
        import linux_artwork
        assert len(linux_artwork.assets(root / 'artwork', list(setup_linux.NAMES))) == 2
        assert len(setup_linux.LANGUAGES) == 14
        assert (root / 'bin/d2rl-pcvr.dll').is_file()
        assert launch_linux.route(['/bin/true'], {}, {}) == (['/bin/true'], {})
        print('Embedded runtime, installation modules and payload: OK')
        return 0
    from setup_linux_gui import run
    return run(root, smoke='--gui-smoke' in args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f'D2R VR Unleashed: Installation worker failed (exit {error.returncode}). '
              'See the download or installation error above. You can retry setup with the same folders.',
              file=sys.stderr)
        raise SystemExit(error.returncode or 1)
    except (OSError, ValueError, KeyError) as error:
        print('D2R VR Unleashed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
