"""Argon2id password hashing.

One module owns every password hash so the parameters stay in one place:
OWASP's argon2id baseline (m=19 MiB, t=2, p=1). The API-key service
account stores password_hash=None and can never log in with a password;
the login path rejects any user without a hash.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP Password Storage Cheat Sheet: argon2id, 19 MiB memory, 2 iterations.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


def hash_password(password: str) -> str:
    """Hash a password for storage; the result embeds its own salt."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-time verification; False on mismatch or a corrupt hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
