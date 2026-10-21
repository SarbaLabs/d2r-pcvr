#!/usr/bin/env python3
"""Build the Linux Qt test installer from an authenticated upstream release ZIP."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
NAME = 'D2R-VR-Unleashed-Linux-Setup'
# The Windows DLL runs under Proton unchanged; this is not a native Linux DLL.
DEFAULT_SHA256 = '368abe50ec9b5dd5b3811809d86ccb96c2a86dddbd789c42b2be259690bbe185'
LINUX_MODULES = ('setup_linux', 'setup_linux_gui', 'linux_setup_entry', 'linux_tls',
                 'linux_artwork', 'linux_preferences', 'launch_linux')


def stage(package, destination, expected_hash):
    if hashlib.sha256(package.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('Release archive SHA-256 mismatch.')
    with zipfile.ZipFile(package) as archive:
        prefix = 'd2r-pcvr-preview/'
        manifest = json.loads(archive.read(prefix + 'release-manifest.json'))
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        hashes = manifest['sha256']
        if len(names) != len(set(names)) or set(names) != {prefix + n for n in hashes} | {prefix + 'release-manifest.json'}:
            raise ValueError('Release contains duplicate or untracked files.')
        for name, digest in hashes.items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                raise ValueError('Unsafe release path: ' + name)
            data = archive.read(prefix + name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Release payload checksum mismatch: ' + name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (destination / 'release-manifest.json').write_text(json.dumps(manifest, indent=2))
    for module in LINUX_MODULES:
        shutil.copy2(ROOT / 'steamvr' / (module + '.py'), destination / 'steamvr')
    shutil.copytree(ROOT / 'artwork/SteamVR', destination / 'artwork/SteamVR')
    shutil.copytree(ROOT / 'artwork/WiVRn', destination / 'artwork/WiVRn')
    shutil.copy2(ROOT / 'INSTALL-LINUX.md', destination)
    shutil.copy2(ROOT / 'THIRD_PARTY.md', destination)
    # All analyzed upstream installer modules must match the packaged runtime data.
    for file in (destination / 'steamvr').glob('*.py'):
        source = ROOT / 'steamvr' / file.name
        if file.stem not in LINUX_MODULES and source.exists() and file.read_text() != source.read_text():
            raise ValueError('Release and checkout differ: ' + file.name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--sha256', default=DEFAULT_SHA256)
    args = parser.parse_args()
    build = ROOT / 'build-frame/linux-setup'
    build.mkdir(parents=True, exist_ok=True)
    payload = Path(tempfile.mkdtemp(prefix='payload-', dir=build))
    manifest = stage(args.package, payload, args.sha256)
    licenses = payload / 'runtime-licenses'
    licenses.mkdir()
    for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pyinstaller'):
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if 'license' in str(file).lower() or 'copying' in str(file).lower():
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = licenses / package / source.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    import builtins
    builtins.license._Printer__setup()
    (licenses / 'PYTHON-LICENSE.txt').write_text('\n'.join(builtins.license._Printer__lines))
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', NAME, '--distpath', str(build / 'dist'), '--workpath', str(build / 'work'),
        '--specpath', str(build), '--paths', str(ROOT / 'steamvr'),
        '--add-data', str(payload) + ':payload', str(ROOT / 'steamvr/linux_setup_entry.py')], check=True)
    bundle = build / 'dist' / NAME
    executable = bundle / NAME
    subprocess.run([str(executable), '--self-test'], check=True)
    subprocess.run([str(executable), '--gui-smoke'], env=dict(os.environ, QT_QPA_PLATFORM='offscreen'), check=True)
    result = subprocess.run([str(executable), '--route', '/bin/sh', '-c', 'exit 17'])
    if result.returncode != 17:
        raise ValueError('Stock routing did not preserve the exit code.')
    embedded = bundle / '_internal/payload'
    for folder in ('SteamVR', 'WiVRn'):
        for source in (ROOT / 'artwork' / folder).glob('*.png'):
            if source.read_bytes() != (embedded / 'artwork' / folder / source.name).read_bytes():
                raise ValueError('Embedded artwork mismatch: ' + str(source))
    shutil.copy2(ROOT / 'INSTALL-LINUX.md', bundle / 'README.md')
    receipt = {'installer_source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'installer_worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
        'plugin_source_commit': manifest['source_commit'], 'upstream_package_sha256': args.sha256,
        'release': manifest.get('release_tag'), 'headset_tested': False,
        'sha256': {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in embedded.rglob('*') if p.is_file()}}
    (bundle / 'build.json').write_text(json.dumps(receipt, indent=2))
    output = build / (NAME + '.tar.gz')
    with tarfile.open(output, 'w:gz') as archive:
        archive.add(bundle, arcname=NAME)
    output.with_suffix('.tar.gz.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest() + '  ' + output.name + '\n')
    print(output)


if __name__ == '__main__':
    main()
