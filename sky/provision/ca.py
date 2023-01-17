# adopted from https://gist.github.com/major/8ac9f98ae8b07f46b208
from typing import Tuple

import datetime
import functools
import os
import pathlib
import uuid

from cryptography import x509
from cryptography.hazmat import backends
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import oid

KEY_DIR = pathlib.Path.home() / '.sky' / 'certs'
KEY_DIR.mkdir(parents=True, exist_ok=True)
CLIENT_KEY_PATH = KEY_DIR / 'client.key'
CLIENT_CERT_PATH = KEY_DIR / 'client.crt'
SERVER_KEY_PATH = KEY_DIR / 'server.key'
SERVER_CERT_PATH = KEY_DIR / 'server.crt'

VALID_DAYS = datetime.timedelta(365 * 100, 0, 0)
ONE_DAY = datetime.timedelta(1, 0, 0)
SUBJECT_NAME = x509.Name([
    x509.NameAttribute(oid.NameOID.COMMON_NAME, 'Skypilot Temporary CA'),
    x509.NameAttribute(oid.NameOID.ORGANIZATION_NAME, 'Skypilot'),
    x509.NameAttribute(oid.NameOID.ORGANIZATIONAL_UNIT_NAME,
                       'Temporary CA Deployment'),
])
ISSUER_NAME = x509.Name([
    x509.NameAttribute(oid.NameOID.COMMON_NAME, 'Skypilot Temporary CA'),
])


def _generate_ca() -> Tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537,
                                           key_size=2048,
                                           backend=backends.default_backend())
    public_key = private_key.public_key()
    builder = x509.CertificateBuilder()
    builder = builder.subject_name(SUBJECT_NAME)
    builder = builder.issuer_name(ISSUER_NAME)
    builder = builder.not_valid_before(datetime.datetime.today() - ONE_DAY)
    builder = builder.not_valid_after(datetime.datetime.today() + VALID_DAYS)
    builder = builder.serial_number(int(uuid.uuid4()))
    builder = builder.public_key(public_key)
    builder = builder.add_extension(
        x509.BasicConstraints(ca=True, path_length=None),
        critical=True,
    )
    certificate = builder.sign(private_key=private_key,
                               algorithm=hashes.SHA256(),
                               backend=backends.default_backend())
    assert isinstance(certificate, x509.Certificate)

    # To encrypt the private key,
    #  use `serialization.BestAvailableEncryption(b"skypilot")`
    ca_key = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption())
    ca_crt = certificate.public_bytes(encoding=serialization.Encoding.PEM)
    return ca_key, ca_crt


def _write_with_chmod(data: bytes, target: pathlib.Path):
    with open(target, 'wb', opener=functools.partial(os.open, mode=0o600)) as f:
        f.write(data)


def generate_cert_file(overwrite_if_exists: bool = False):
    if (not CLIENT_KEY_PATH.exists() or not CLIENT_CERT_PATH.exists() or
            overwrite_if_exists):
        ca_key, ca_crt = _generate_ca()
        _write_with_chmod(ca_key, CLIENT_KEY_PATH)
        _write_with_chmod(ca_crt, CLIENT_CERT_PATH)
    if (not SERVER_KEY_PATH.exists() or not SERVER_CERT_PATH.exists() or
            overwrite_if_exists):
        ca_key, ca_crt = _generate_ca()
        _write_with_chmod(ca_key, SERVER_KEY_PATH)
        _write_with_chmod(ca_crt, SERVER_CERT_PATH)


if __name__ == '__main__':
    ca_key, ca_crt = _generate_ca()
    print(f'{ca_key.decode("ascii")}\n\n{ca_crt.decode("ascii")}')
