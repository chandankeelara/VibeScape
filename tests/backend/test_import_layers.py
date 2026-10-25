"""The layering the app.py split set up (refactor PRs 2-6), read from the
source with ast, so a function-local import counts too:

  app.py           imports routers and assembles them; nothing imports it.
  backend/routers  import services, deps and the flat modules; never app.py
                   and never another router (routers.Router / register,
                   from the package itself, are the exception).
  backend/services and every other backend module never import routers or
                   app.py.

A router importing app.py would be circular (app.py imports every router),
and a service importing a router would let route code leak back below it.
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
ROUTER_MODULES = {p.stem for p in (BACKEND / "routers").glob("*.py") if p.stem != "__init__"}
PACKAGE_NAMES = {"Router", "register"}       # what a router may take from `routers`


def imported(path: Path) -> list[str]:
    """Every module path `path` imports, anywhere in the file, written as
    the dotted name it resolves to from backend/ (the import root)."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}: relative import (backend/ is the root)"
            out += [f"{node.module}.{a.name}" for a in node.names]
    return out


def _files(sub=None):
    base = BACKEND / sub if sub else BACKEND
    return sorted(base.glob("*.py"))


def _id(p):
    return str(p.relative_to(BACKEND))


@pytest.mark.parametrize("path", _files("routers"), ids=_id)
def test_a_router_imports_neither_app_nor_another_router(path):
    bad = []
    for name in imported(path):
        parts = name.split(".")
        if parts[0] == "app":
            bad.append(name)
        elif parts[0] == "routers" and len(parts) > 1 and parts[1] not in PACKAGE_NAMES:
            bad.append(name)
    assert not bad, f"{_id(path)} imports {bad}"


@pytest.mark.parametrize("path", _files("services") + [p for p in _files() if p.stem != "app"],
                         ids=_id)
def test_nothing_below_the_routers_imports_them_or_app(path):
    bad = [n for n in imported(path) if n.split(".")[0] in ("routers", "app")]
    assert not bad, f"{_id(path)} imports {bad}"


def test_the_check_sees_every_router():
    """A guard on the guard: the routers app.py registers are the files
    checked above."""
    registered = {n.split(".")[1] for n in imported(BACKEND / "app.py")
                  if n.startswith("routers.") and n.split(".")[1] not in PACKAGE_NAMES}
    assert registered == ROUTER_MODULES
