"""Password-hashing (stdlib pbkdf2) og slug-validering."""
import hashlib
import hmac
import os
import re

ITERATIONS = 120_000

# Navne der ikke må bruges som gruppe- eller event-slug, så de aldrig kolliderer
# med /master eller /<gruppe>/admin osv.
RESERVED_SLUGS = {
    "master", "admin", "login", "logout", "static", "api",
    "settings", "events", "new", "edit", "delete", "export", "image",
}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = text.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def is_valid_slug(slug: str) -> bool:
    """Gyldig slug der ikke er reserveret."""
    if not slug or slug in RESERVED_SLUGS:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug))
