"""
Raises: [OSError, TypeError]
"""

import ssl
from functools import lru_cache

import httpx


@lru_cache(maxsize=32)
def context(verify: bool | str) -> ssl.SSLContext:
    """
    The TLS context of an HTTP client for the `ssl_verify` of a plugin's
    details (true, false or the path of a CA file), to be passed as its
    `verify`.

    Building one means loading the CA bundle, which takes far longer than
    most of the requests it is built for and blocks the event loop while it
    does -- and a client would do that each time it is created. So there is
    one per value for the lifetime of the process (a CA file is not read
    again when it changes).

    Raises OSError for a CA file that cannot be loaded and TypeError for a
    value that is none of the above.
    """
    return httpx.create_ssl_context(verify=verify)
