"""Create a signed OneNET application package on the Windows build host."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import re
import tarfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from ota_core import version_tuple

ROOT = Path(__file__).resolve().parent
ALLOWED = {'QDesktop', 'camera_web.py', 'index.html', 'onenet_agent.py',
           'voice_service.py', 'voice_core.py'}


def make_package(files, version, base_version, output, key_file=None):
    if version_tuple(version) <= version_tuple(base_version):
        raise ValueError('The target version must be newer than the source version')
    product_id = os.environ.get('WEIJU_PRODUCT_ID', '')
    if not re.match(r'^[A-Za-z0-9]{6,64}$', product_id):
        raise ValueError('Set WEIJU_PRODUCT_ID to your OneNET product ID')
    key_file = Path(key_file or ROOT / 'private/signing-key.pem')
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    payloads = {name: Path(path).read_bytes() for name, path in files.items()}
    if not payloads or not set(payloads) <= ALLOWED:
        raise ValueError('Unsupported or missing payload')
    manifest = {'format': 1, 'kind': 'weiju-apps', 'board': 'imx6ull-512-emmc',
                'product_id': product_id, 'version': version, 'from_version': base_version,
                'files': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in payloads.items()}}
    raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    signature = key.sign(raw, padding.PKCS1v15(), hashes.SHA256())
    with tarfile.open(str(output), 'w:gz') as archive:
        for name, data in dict(payloads, **{'manifest.json': raw, 'manifest.sig': signature}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--from-version', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--qdesktop')
    parser.add_argument('--camera-web')
    parser.add_argument('--index')
    parser.add_argument('--onenet-agent')
    parser.add_argument('--voice-service')
    parser.add_argument('--voice-core')
    args = parser.parse_args()
    sources = {name: path for name, path in [('QDesktop', args.qdesktop),
               ('camera_web.py', args.camera_web), ('index.html', args.index),
               ('onenet_agent.py', args.onenet_agent),
               ('voice_service.py', args.voice_service),
               ('voice_core.py', args.voice_core)] if path}
    manifest = make_package(sources, args.version, args.from_version, args.output)
    print('SIGNED_PACKAGE', args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
