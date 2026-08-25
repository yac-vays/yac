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


def _is_plugin_function(obj: object) -> bool:
    """
    A plugin member is usable as a j2 function/filter/test if it is a plain
    (possibly async) function, or a function wrapped by a caching/decorator
    that exposes the original via __wrapped__ (e.g. functools.lru_cache,
    async_lru.alru_cache). The latter are callable wrapper objects that
    inspect.isfunction rejects, which would otherwise silently drop decorated
    builtins like host_in_ip4ranges.
    """
    if isfunction(obj):
        return True
    return callable(obj) and isfunction(getattr(obj, "__wrapped__", None))


@lru_cache(maxsize=None)
def get_functions(kind: str) -> dict[str, FunctionType]:
    functions = {}
    kind_dir = _kind_dir(kind)
    try:
        files = glob.glob(f"{kind_dir}/*.py")
    except OSError as error:
        raise PluginError(f"Could not read {kind} plugin dir: {error}") from error
    for file in files:
        if file == f"{kind_dir}/__init__.py":
            continue
        logger.info(f"Loading plugin {file}")
        try:
            module = pydoc.importfile(file)
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
        for function in getmembers(module, _is_plugin_function):
            logger.debug(f"Loading function {function[0]} from plugin {file}")
            functions.update({function[0]: getattr(module, function[0])})
    return functions


@lru_cache(maxsize=None)
def _load_modules(kind: str) -> dict[str, ModuleType]:
    modules = {}
    kind_dir = _kind_dir(kind)
    try:
        files = glob.glob(f"{kind_dir}/*.py")
    except OSError as error:
        raise PluginError(f"Could not read {kind} plugin dir: {error}") from error
    for file in files:
        if file == f"{kind_dir}/__init__.py":
            continue
        logger.info(f"Loading plugin {file}")
        try:
            module = pydoc.importfile(file)
            modules.update({Path(file).stem: module})
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
    return modules


def get_modules(kind: str, require: tuple[str] | None = None) -> dict[str, ModuleType]:
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
    kind: str, varname: str, *, require: tuple[str] | None = None, late: bool = False
) -> list[ModuleType]:
    """
    Modules of this kind need to implement a order() function that returns
    a tuple of a bool and an int. The bool determins if the module is meant
    for late (True) or early (False) execution and the int is a order number
    within the execution time (late/early), so a higher number runs later.
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
