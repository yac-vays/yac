"""
Compatibility shim: the suite now runs under pytest (see `pytest.ini` and
`conftest.py`). The build pipeline invokes `pytest` directly; this entry point is
kept so the historical `PYTHONPATH=. python tests/main.py` command still works by
delegating to pytest over this directory.
"""

import os
import sys

if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([os.path.dirname(os.path.abspath(__file__)), "-q"]))
