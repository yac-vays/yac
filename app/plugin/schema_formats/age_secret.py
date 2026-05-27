"""
Schema-format plugin: ``age_secret``.

Validates that a string is an ASCII-armored `AGE <https://age-encryption.org>`_
ciphertext (the format produced by ``age --armor`` and by the VAYS
``age_secret`` renderer).

This is a structural check only: it does not (and cannot) verify that the
ciphertext is decryptable - YAC has no access to the matching AGE identity.
What is checked:

  - the armor header / footer are exactly the standard PEM markers,
  - the body is well-formed base64,
  - the decoded body starts with the canonical AGE v1 header
    ``age-encryption.org/v1\\n``.

Spec authors can attach this to a string field with ``format: age_secret``
to reject values that are obviously not AGE ciphertexts (typos, plaintext
left behind, wrong key material pasted in, ...).
"""

import base64
import binascii

_ARMOR_HEADER = "-----BEGIN AGE ENCRYPTED FILE-----"
_ARMOR_FOOTER = "-----END AGE ENCRYPTED FILE-----"
_AGE_V1_PREFIX = b"age-encryption.org/v1\n"

# Loose lower bound on the decoded payload (v1 header + one X25519 stanza +
# the encrypted-payload nonce add up to well over 100 bytes in practice).
# Kept conservative so harmless format quirks don't trigger false negatives.
_MIN_DECODED_LEN = 64


def age_secret(data) -> bool:
    if not isinstance(data, str):
        return False

    lines = data.strip().splitlines()
    if len(lines) < 3:
        return False
    if lines[0].strip() != _ARMOR_HEADER:
        return False
    if lines[-1].strip() != _ARMOR_FOOTER:
        return False

    body = "".join(line.strip() for line in lines[1:-1])
    if not body:
        return False

    try:
        decoded = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return False

    if not decoded.startswith(_AGE_V1_PREFIX):
        return False
    if len(decoded) < _MIN_DECODED_LEN:
        return False

    return True
