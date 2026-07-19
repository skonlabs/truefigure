"""Reversible at-rest encryption for secrets that must be USED, not just verified.

API-key secrets are verify-only, so they are SHA-256 hashed (one-way). A webhook
signing secret is different: the delivery worker must HMAC outgoing payloads with
the SAME secret the integrator holds (v1=hex(hmac_sha256(secret, ts.body))), so it
must be recoverable. This module stores it encrypted, never in plaintext.

Dev/CI uses a keyed keystream cipher with the key from TF_SECRET_KEY. In
production this maps to a KMS-managed key (or pgcrypto pgp_sym_encrypt); the
interface is the same. The webhooks column is named secret_hash for historical
reasons — for webhooks it holds this ciphertext (recorded in discrepancies).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def _key() -> bytes:
    return hashlib.sha256((os.environ.get("TF_SECRET_KEY") or "tf-dev-secret-key").encode()).digest()


def _keystream(nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(_key(), nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    data = plaintext.encode("utf-8")
    nonce = hashlib.sha256(data + _key()).digest()[:8]  # deterministic nonce -> idempotent create
    ct = bytes(b ^ k for b, k in zip(data, _keystream(nonce, len(data)), strict=True))
    return "enc1:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str) -> str:
    if not token.startswith("enc1:"):
        raise ValueError("not an enc1 token")
    blob = base64.urlsafe_b64decode(token[len("enc1:"):].encode())
    nonce, ct = blob[:8], blob[8:]
    pt = bytes(b ^ k for b, k in zip(ct, _keystream(nonce, len(ct)), strict=True))
    return pt.decode("utf-8")
