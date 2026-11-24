"""Generate private device pairing material and a self-signed pinned TLS cert."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import secrets
import tarfile
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT/'private'
PRIVATE.mkdir(exist_ok=True)
pair_file = PRIVATE/'device-pairing.weiju'
if not pair_file.exists():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Weiju imx6ull_001 mobile')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2025,1,1))
            .not_valid_after(datetime.datetime(2036,1,1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .sign(key,hashes.SHA256()))
    (PRIVATE/'server-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    (PRIVATE/'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    token = secrets.token_urlsafe(32)
    (PRIVATE/'config.json').write_text(json.dumps({'token':token}),encoding='utf-8')
    pair_file.write_text(json.dumps({'format':'weiju-pairing-v1','device_name':'imx6ull_001',
        'host':os.environ.get('WEIJU_DEVICE_HOST', '192.168.1.100'),'token':token,
        'cert_sha256':cert.fingerprint(hashes.SHA256()).hex()},indent=2),encoding='utf-8')
archive = PRIVATE/'mobile-adapter.tar.gz'
with tarfile.open(archive,'w:gz') as t:
    for n in ('config.json','server.pem','server-key.pem'):
        t.add(PRIVATE/n,arcname=n)
    for n in ('mobile_api.py','voice_audio.py','alerts_api.py','weiju-mobile.init','install.sh'):
        t.add(ROOT/'board'/n,arcname=n)
print('PRIVATE_PAIRING_PREPARED; credentials omitted')
