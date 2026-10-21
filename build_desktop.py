"""Build the native macOS shell using the installed Apple toolchain."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
from install import write


def build(runtime, appdir):
    runtime, appdir = runtime.resolve(), appdir.expanduser().absolute()
    if appdir.is_symlink():
        raise ValueError('Refusing linked app')
    if appdir.exists():
        info = plistlib.loads((appdir / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'local.ternpilot.launcher':
            raise ValueError('Unrecognized app; will not overwrite')
    with tempfile.TemporaryDirectory(prefix='ternpilot-native-') as temp:
        binary = Path(temp) / 'TernPilot'
        subprocess.run(['/usr/bin/xcrun', 'swiftc', str(runtime / 'Desktop.swift'), '-O',
                        '-module-cache-path', str(Path(temp) / 'modules'), '-framework', 'AppKit', '-framework', 'WebKit', '-o', str(binary)], check=True)
        contents = appdir / 'Contents'
        (contents / 'MacOS').mkdir(parents=True, exist_ok=True)
        (contents / 'Resources').mkdir(parents=True, exist_ok=True)
        target = contents / 'MacOS/TernPilot'
        if target.is_symlink():
            raise ValueError('Refusing linked executable')
        staged = target.with_suffix('.new')
        shutil.copy2(binary, staged); staged.replace(target)
        info = {'CFBundleName': 'TernPilot', 'CFBundleDisplayName': 'TernPilot',
                'CFBundleIdentifier': 'local.ternpilot.launcher', 'CFBundleVersion': '0.4.0',
                'CFBundleShortVersionString': '0.4.0', 'CFBundleExecutable': 'TernPilot',
                'CFBundlePackageType': 'APPL', 'NSHighResolutionCapable': True,
                'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}
        write(contents / 'Info.plist', plistlib.dumps(info).decode(), 0o644)
        write(contents / 'Resources/runtime-path.txt', str(runtime), 0o644)
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(appdir)], check=True)
    print('Native app installed: ' + str(appdir))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--app-dir', type=Path, default=Path.home() / 'Applications/TernPilot.app')
    args = parser.parse_args()
    build(args.runtime, args.app_dir)
