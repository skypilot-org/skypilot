# adopted from https://gist.github.com/major/8ac9f98ae8b07f46b208
from typing import Tuple

import datetime
import uuid

from cryptography import x509
from cryptography.hazmat import backends
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509 import oid

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


def generate_ca() -> Tuple[bytes, bytes]:
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

    ca_key = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.BestAvailableEncryption(b"skypilot"))
    ca_crt = certificate.public_bytes(encoding=serialization.Encoding.PEM)
    return ca_key, ca_crt


if __name__ == '__main__':
    ca_key, ca_crt = generate_ca()
    print(f'{ca_key.decode("ascii")}\n\n{ca_crt.decode("ascii")}')
