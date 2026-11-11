"""Create a local lab CA and HTTPS certificate; never installs trust automatically."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', action='append', required=True, help='Exact LAN IPv4 or DNS name, repeatable')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / '.secret-video-tls'
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        parser.error('Certificate directory is not empty; existing keys are never overwritten')
    names = []
    for host in ['localhost', '127.0.0.1', *args.host]:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            if not host or any(c in host for c in '/:*?@ \\'):
                parser.error('Use exact IP / DNS names, without scheme or port')
            names.append(x509.DNSName(host))
    now = datetime.now(timezone.utc)
    ca_key, key = rsa.generate_private_key(public_exponent=65537, key_size=2048), rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'ISTG SecretVideo Local Lab CA')])
    ca = (x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.host[0])]))
            .issuer_name(issuer).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    for name, value in [('ca.key', ca_key), ('server.key', key)]:
        (output / name).write_bytes(value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    for name, value in [('ca.crt', ca), ('server.crt', cert)]:
        (output / name).write_bytes(value.public_bytes(serialization.Encoding.PEM))
    print(f'Created {output}. Trust ca.crt only on participating lab PCs. Never share ca.key or server.key.')


if __name__ == '__main__':
    main()
