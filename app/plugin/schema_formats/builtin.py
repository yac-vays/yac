import base64
import binascii
import re
import struct


def ssh_key(data) -> bool:
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
        r"^\$5\$.{0,16}\$[./0-9A-Za-z]{43}$",  # SHA-256
        r"^\$6\$.{0,16}\$[./0-9A-Za-z]{86}$",  # SHA-512
        r"^\$2[aby]\$[0-9]{2}\$[./0-9A-Za-z]{53}$",  # Bcrypt
    ]

    return any(re.match(pattern, data) for pattern in patterns)
