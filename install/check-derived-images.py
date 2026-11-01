#!/usr/bin/env python3
"""Read-only validation of the shared QEMU image directory (run on a CephFS node)."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


def info(path):
    result = subprocess.run(['qemu-img', 'info', '-U', '--output=json', str(path)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def check(directory, images_file=None):
    directory = Path(directory)
    errors, checked, bases = [], 0, {}
    pins = {}
    if images_file is not None:
        for image in json.loads(Path(images_file).read_text())['images']:
            if image.get('kind') == 'qemu' and image.get('sha256'):
                pins[image['file']] = image['sha256']
    if directory.is_symlink() or not directory.is_dir():
        return {'ok': False, 'images': 0, 'errors': ['QEMU directory must be a real directory']}
    for path in sorted(directory.glob('d-*.qcow2')):
        checked += 1
        try:
            if not re.fullmatch(r'd-[0-9a-f]{12}\.qcow2', path.name):
                raise ValueError('invalid derived image name')
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('image must be a regular file without symlinks')
            if stat.S_IMODE(path.stat().st_mode) != 0o444:
                raise ValueError('image mode must be 0444')
            metadata = info(path)
            backing = metadata.get('backing-filename', '')
            if metadata.get('format') != 'qcow2' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.qcow2', backing):
                raise ValueError('backing must be one relative qcow2 filename')
            if backing.startswith('d-'):
                raise ValueError('backing must be an administrator base, never another derived image')
            base = directory / backing
            if base.is_symlink() or not base.is_file():
                raise ValueError('backing file is missing or is a symlink')
            if backing not in bases:
                base_info = info(base)
                if base_info.get('format') != 'qcow2' or base_info.get('backing-filename'):
                    raise ValueError('administrator base must be standalone qcow2')
                if images_file is not None:
                    if not re.fullmatch(r'sha256:[0-9a-f]{64}', pins.get(backing, '')):
                        raise ValueError('base has no pinned SHA-256 in IMAGES_FILE')
                    with base.open('rb') as source:
                        digest = 'sha256:' + hashlib.file_digest(source, 'sha256').hexdigest()
                    if digest != pins[backing]:
                        raise ValueError('base SHA-256 does not match IMAGES_FILE')
                bases[backing] = True
            checksum = path.with_name(path.name + '.md5sum')
            if checksum.is_symlink() or not checksum.is_file() or stat.S_IMODE(checksum.stat().st_mode) != 0o444:
                raise ValueError('MD5 sidecar must be a regular file with mode 0444')
            if not re.fullmatch(r'[0-9a-f]{32}', checksum.read_text().strip()):
                raise ValueError('MD5 sidecar must contain a GNS3 hexadecimal digest')
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
            errors.append(f'{path.name}: {exc}')
    return {'ok': not errors, 'images': checked, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu_directory', type=Path)
    parser.add_argument('--images-file', type=Path, help='Also verify pinned base SHA-256 from IMAGES_FILE')
    args = parser.parse_args()
    try:
        result = check(args.qemu_directory, args.images_file)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result = {'ok': False, 'images': 0, 'errors': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
