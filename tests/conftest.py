"""
Shared pytest setup and fixtures for the YAC test suite.

Importing most of `app.lib.*` triggers a specs read at import time (the repo
layer initialises its plugin from the specs), which calls `sys.exit` if the file
is missing. So we point `YAC_SPECS` at a minimal in-repo specs file *before* any
test module imports app code -- this module is imported by pytest first.
"""

import os
from pathlib import Path

# Must run before any `import app.*` below or in the collected test modules.
os.environ.setdefault(
    "YAC_SPECS", str(Path(__file__).parent / "fixtures" / "minimal.yml")
)

import pytest  # noqa: E402

from app.model.plg import IRepoSession  # noqa: E402


class FakeRepoSession(IRepoSession):
    """
    In-memory `IRepoSession` for tests. `files` maps entity name -> YAML text;
    `links` maps a link entity name -> the entity name it points at. Reads follow
    links (like a real symlink); `get_resolved` reports the target so the limits
    layer can be exercised.
    """

    def __init__(self, files=None, links=None):
        self.files = dict(files or {})
        self.links = dict(links or {})

    async def get_hash(self):
        return "testhash"

    async def list(self, type):
        return sorted([*self.files, *self.links])

    async def exists(self, type, name):
        return name in self.files or name in self.links

    async def is_link(self, type, name):
        return name in self.links

    async def get_link(self, type, name):
        if name not in self.links:
            raise Exception(f"{name} is not a link")
        return self.links[name]

    async def get(self, type, name):
        return self.files[self.links.get(name, name)]

    # Writers are unused by the read/validate/limit paths under test.
    async def write(self, *a):
        raise NotImplementedError

    async def write_rename(self, *a):
        raise NotImplementedError

    async def copy(self, *a):
        raise NotImplementedError

    async def link(self, *a):
        raise NotImplementedError

    async def delete(self, *a):
        raise NotImplementedError


@pytest.fixture
def fake_repo():
    """Factory fixture: `fake_repo(files=..., links=...)` -> FakeRepoSession."""
    return FakeRepoSession
