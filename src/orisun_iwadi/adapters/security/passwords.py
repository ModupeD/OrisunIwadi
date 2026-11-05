"""PasswordHasher using scrypt from Python's standard library (memory-hard, salted).

Stored format: scrypt$<n>$<r>$<p>$<salt>$<hash>, so the cost can be raised later and old
hashes still verify.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

N, R, P = 2**14, 8, 1  # ~16 MB of memory per hash; standard interactive-login settings


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class ScryptHasher:
    def hash(self, password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
        return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            algo, n, r, p, salt, digest = password_hash.split("$")
            if algo != "scrypt":
                return False
            actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                    n=int(n), r=int(r), p=int(p))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, base64.b64decode(digest))  # constant-time compare
