"""A self-hosted deployment's Ed25519 keypair — its identity to the central
knowledge service.

Generated on first use and persisted to disk; the private key never leaves the
box. The **fingerprint** (``sha256`` of the public key, PEM-encoded) is what
gets registered centrally and what a signed request's JWT ``kid`` names, so
central can look up the matching public key and verify the signature without
ever seeing the private key.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_DEFAULT_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "site_key.pem"
KEY_PATH = Path(os.environ.get("LESARIN_SITE_KEY", _DEFAULT_KEY_PATH))

_JWT_TTL_SECONDS = 60  # signed requests are short-lived; replay beyond this fails


def _private_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def fingerprint(public_pem: bytes) -> str:
    return hashlib.sha256(public_pem).hexdigest()


def _load_or_create(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_private_pem(key))
    try:
        path.chmod(0o600)  # private key: owner read/write only
    except OSError:
        pass  # best-effort on filesystems that don't support unix perms
    return key


class SiteIdentity:
    """This deployment's keypair, loaded once and reused for every signed
    request to central."""

    def __init__(self, key_path: Optional[Path] = None):
        self.path = Path(key_path) if key_path else KEY_PATH
        self.private_key = _load_or_create(self.path)
        self.public_pem = _public_pem(self.private_key)
        self.fingerprint = fingerprint(self.public_pem)

    def signed_jwt(self, claims: Optional[dict] = None, ttl_seconds: int = _JWT_TTL_SECONDS) -> str:
        """A short-lived EdDSA JWT proving this site's identity, ``kid`` = its
        fingerprint so central can look up the public key to verify against."""
        now = int(time.time())
        payload = {**(claims or {}), "iat": now, "exp": now + ttl_seconds, "fp": self.fingerprint}
        return jwt.encode(
            payload, _private_pem(self.private_key), algorithm="EdDSA",
            headers={"kid": self.fingerprint},
        )


_identity: Optional[SiteIdentity] = None


def get_identity() -> SiteIdentity:
    """Process-wide singleton — the keypair is loaded from disk once."""
    global _identity
    if _identity is None:
        _identity = SiteIdentity()
    return _identity
