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


@lru_cache(maxsize=None)
def get_functions(kind: str) -> dict[str, FunctionType]:
    functions = {}
    try:
        files = glob.glob(f"/code/app/plugin/{kind}/*.py")
    except OSError as error:
        raise PluginError(f"Could not read {kind} plugin dir: {error}") from error
    for file in files:
        if file == f"/code/app/plugin/{kind}/__init__.py":
            continue
        logger.info(f"Loading plugin {file}")
        try:
            module = pydoc.importfile(file)
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
        for function in getmembers(module, isfunction):
            logger.debug(f"Loading function {function[0]} from plugin {file}")
            functions.update({function[0]: getattr(module, function[0])})
    return functions


@lru_cache(maxsize=None)
def get_modules(kind: str, require: tuple[str] | None = None) -> dict[str, ModuleType]:
    modules = {}
    try:
        files = glob.glob(f"/code/app/plugin/{kind}/*.py")
    except OSError as error:
        raise PluginError(f"Could not read {kind} plugin dir: {error}") from error
    for file in files:
        if file == f"/code/app/plugin/{kind}/__init__.py":
            continue
        logger.info(f"Loading plugin {file}")
        try:
            module = pydoc.importfile(file)
            modules.update({Path(file).stem: module})
        except (pydoc.ErrorDuringImport, ImportError, OSError, SyntaxError) as error:
            raise PluginError(f"Could not import plugin {file}: {error}") from error
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
def get_modules_sorted(
    kind: str, *, require: tuple[str] | None = None, late: bool = False
) -> list[ModuleType]:
    """
    Modules of this kind need to implement a order() function that returns
    a tuple of a bool and an int. The bool determins if the module is meant
    for late (True) or early (False) execution and the int is a order number
    within the execution time (late/early), so a higher number runs later.
    """
    modules = []
    for module in sorted(get_modules(kind, require).values(), key=cmp_to_key(__sort)):
        try:
            po, _ = module.order()
            assert isinstance(po, bool)
        except (AttributeError, AssertionError):
            po = False
        if po == late:
            modules.append(module)
    return modules


@lru_cache(maxsize=None)
def get_module(kind: str, module: str) -> ModuleType:
    file = f"/code/app/plugin/{kind}/{module}.py"
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
