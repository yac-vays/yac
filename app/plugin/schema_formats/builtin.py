import base64
import binascii
import re
import struct


def ssh_key(data) -> bool:
    # Exactly one key: a value with further lines would add arbitrary lines
    # to whatever file (authorized_keys, ...) the key ends up in.
    if not isinstance(data, str) or re.search(r"[\r\n\x00]", data.strip()):
        return False
    parts = data.strip().split()
    if len(parts) < 2:
        return False

    # Extract key type from base64 data
    try:
        data = base64.b64decode(parts[1], validate=True)
        key_type = data[4 : 4 + struct.unpack(">I", data[:4])[0]].decode("utf-8")
    except (binascii.Error, ValueError, struct.error, UnicodeDecodeError):
        return False

    # Compare with plain key type
    if key_type != parts[0]:
        return False

    return True


def unix_password_hash(data) -> bool:
    if not isinstance(data, str):
        return False

    patterns = [
        r"^\$5\$(rounds=[0-9]+\$)?[^$:\s]{0,16}\$[./0-9A-Za-z]{43}$",  # SHA-256
        r"^\$6\$(rounds=[0-9]+\$)?[^$:\s]{0,16}\$[./0-9A-Za-z]{86}$",  # SHA-512
        r"^\$2[aby]\$[0-9]{2}\$[./0-9A-Za-z]{53}$",  # Bcrypt
    ]

    # fullmatch: with re.match, `$` would also accept a trailing newline
    return any(re.fullmatch(pattern, data) for pattern in patterns)
