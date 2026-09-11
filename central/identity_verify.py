"""Central's half of site identity: parse a submitted public key and derive
its fingerprint the same way ``site_agent.identity`` does, independent of
incidental whitespace differences introduced by JSON transport — the key is
re-serialized deterministically before hashing rather than hashing the
transported bytes verbatim.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(pem)


def canonical_pem(pem: bytes) -> bytes:
    pub = load_public_key(pem)
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def fingerprint_of_pem(pem: bytes) -> str:
    return hashlib.sha256(canonical_pem(pem)).hexdigest()
