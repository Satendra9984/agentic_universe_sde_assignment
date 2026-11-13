"""Phone join keys and name encryption.

The raw phone is never written to SQLite. Events and leads join on an HMAC of
the canonical ``+91`` form, so a dump of the database cannot be turned back
into a phone book by enumerating Indian mobiles. The last four digits are
stored separately because the lead report must print them.

``customer_name`` is encrypted. No report prints it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet

# Assignment-local secrets. Synthetic data only. Documented in SUBMISSION.md.
PHONE_HMAC_KEY = b"au-assignment-a-phone-hmac-v1"
_NAME_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"au-assignment-a-name-key-v1").digest())
_FERNET = Fernet(_NAME_KEY)


def phone_hmac(canonical: str) -> str:
    return hmac.new(PHONE_HMAC_KEY, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_name(name: str) -> str:
    return _FERNET.encrypt(name.encode("utf-8")).decode("ascii")


def decrypt_name(token: str) -> str:
    return _FERNET.decrypt(token.encode("ascii")).decode("utf-8")
