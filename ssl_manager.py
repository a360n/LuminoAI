#!/usr/bin/env python3
"""
LuminoAI Automated TLS/SSL Certificate Manager
---------------------------------------------
Generates high-security self-signed TLS/SSL certificates for local LAN access.
Enables modern smartphone mobile browsers (iOS Safari, Android Chrome) to unlock
the camera hardware stream (navigator.mediaDevices.getUserMedia) for real-time live video barcode scanning.
"""

import os
import socket
import datetime
import ipaddress
import logging
from typing import Tuple, Optional

logger = logging.getLogger("lumino.ssl")

def get_lan_ip_addresses():
    """Returns all non-loopback IPv4 addresses on the host."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        except Exception:
            pass
        finally:
            s.close()
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.append(ip)
    except Exception:
        pass

    return ips

def ensure_ssl_certificates(
    cert_path: str = "ssl_cert.pem",
    key_path: str = "ssl_key.pem",
    force_regenerate: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """
    Ensures that a valid TLS certificate and private key exist.
    If not, automatically generates an x509 certificate with Subject Alternative Names (SAN)
    for localhost, 127.0.0.1, and all discovered LAN IPv4 addresses.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_cert = os.path.join(base_dir, cert_path) if not os.path.isabs(cert_path) else cert_path
    full_key = os.path.join(base_dir, key_path) if not os.path.isabs(key_path) else key_path

    if not force_regenerate and os.path.exists(full_cert) and os.path.exists(full_key):
        return full_cert, full_key

    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        # Generate RSA 2048 key
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LuminoAI"),
            x509.NameAttribute(NameOID.COMMON_NAME, "LuminoAI Rework Station"),
        ])

        # SAN entries
        san_entries = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]

        for ip in get_lan_ip_addresses():
            try:
                san_entries.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
            except Exception:
                pass

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .sign(key, hashes.SHA256())
        )

        with open(full_key, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))

        with open(full_cert, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"✅ Generated local TLS/SSL certificate: {full_cert}")
        return full_cert, full_key

    except Exception as e:
        logger.warning(f"Could not generate self-signed TLS certificate: {e}")
        return None, None

if __name__ == "__main__":
    c, k = ensure_ssl_certificates()
    print("Cert:", c, "Key:", k)
