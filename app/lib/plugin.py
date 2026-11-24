"""
Raises: [app.model.err.PluginError]
"""

import glob
import logging
import pydoc
from types import ModuleType
from types import FunctionType
from pathlib import Path
from functools import cmp_to_key
from functools import lru_cache
from inspect import getmembers, isfunction

from app.model.err import PluginError

logger = logging.getLogger(__name__)

# Derive the plugin root from this file's location so the same code works in
# the production container (under /code/app/lib/plugin.py) and in dev / tests
# (anywhere on disk). plugin.py lives at app/lib/plugin.py; siblings are at
# app/plugin/{kind}/*.py.
_PLUGIN_ROOT = (Path(__file__).resolve().parent.parent / "plugin").as_posix()


def _kind_dir(kind: str) -> str:
    return f"{_PLUGIN_ROOT}/{kind}"


def _is_plugin_function(name: str, obj: object, module: ModuleType) -> bool:
    """
    Usable as a j2 function/filter/test or schema format: a public (possibly
    async) function defined in the plugin module itself -- not one it merely
    imports. Functions wrapped by a caching decorator that exposes the
    original via __wrapped__ (functools.lru_cache, async_lru.alru_cache)
    count as well.
    """
    if name.startswith("_"):
        return False
    func = obj if isfunction(obj) else getattr(obj, "__wrapped__", None)
    if not callable(obj) or not isfunction(func):
        return False
    return getattr(func, "__module__", None) == module.__name__


def _plugin_files(kind: str) -> list[str]:
    """The plugin files of a kind, in a stable (alphabetical) order."""
    kind_dir = _kind_dir(kind)
    try:
        files = sorted(glob.glob(f"{kind_dir}/*.py"))
    except OSError as error:
        raise PluginError(f"Could not read {kind} plugin dir: {error}") from error
    return [f for f in files if f != f"{kind_dir}/__init__.py"]


@lru_cache(maxsize=None)
def get_functions(kind: str) -> dict[str, FunctionType]:
    functions = {}
    for file in _plugin_files(kind):
        logger.info(f"Loading plugin {file}")
        try:
            module = pydoc.importfile(file)
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
        for name, obj in getmembers(module):
            if _is_plugin_function(name, obj, module):
                logger.debug(f"Loading function {name} from plugin {file}")
                functions[name] = obj
    return functions


@lru_cache(maxsize=None)
def _load_modules(kind: str) -> dict[str, ModuleType]:
    modules = {}
    for file in _plugin_files(kind):
        logger.info(f"Loading plugin {file}")
        try:
            modules[Path(file).stem] = pydoc.importfile(file)
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
    return modules


def get_modules(kind: str, require: tuple[str, ...] | None = None) -> dict[str, ModuleType]:
    modules = _load_modules(kind)
    if not set(modules.keys()).issuperset(set(require or [])):
        missing = list(set(require or []).difference(modules.keys()))
        raise PluginError(
            f'Could not load required {kind} plugin(s): {", ".join(missing)}'
        )
    return modules


def __sort(plugin1, plugin2) -> int:
    try:
        _, p1 = plugin1.order()
        assert isinstance(p1, int)
    except (AttributeError, AssertionError):
        p1 = 0
    try:
        _, p2 = plugin2.order()
        assert isinstance(p2, int)
    except (AttributeError, AssertionError):
        p2 = 0
    return p1 - p2


@lru_cache(maxsize=None)
def get_sorted(
    kind: str,
    varname: str,
    *,
    require: tuple[str, ...] | None = None,
    late: bool = False,
) -> list:
    """
    The `varname` objects of all modules of this kind whose order() returns
    `late` as its bool, sorted by its int (a higher number runs later). What
    the bool means depends on the kind (see the interfaces in model/plg.py).
    """
    vars = []
    for var in sorted(
        [getattr(m, varname) for m in get_modules(kind, require).values()],
        key=cmp_to_key(__sort),
    ):
        try:
            po, _ = var.order()
            assert isinstance(po, bool)
        except (AttributeError, AssertionError):
            po = False
        if po == late:
            vars.append(var)
    return vars


@lru_cache(maxsize=None)
def get_module(kind: str, module: str) -> ModuleType:
    file = f"{_kind_dir(kind)}/{module}.py"
    logger.info(f"Loading plugin {file}")
    try:
        return pydoc.importfile(file)
    except (
        pydoc.ErrorDuringImport,
        FileNotFoundError,
        ImportError,
        OSError,
        SyntaxError,
    ) as error:
        raise PluginError(f"Could not import plugin {file}: {error}") from error
