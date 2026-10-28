"""The backend runs on Python 3.11 and up (docs/dev/debugging.md): CI and
the Docker image on 3.12, the iPad on 3.13, a checkout's venv maybe on a
newer one. What a newer Python accepts and an older one refuses fails here
too: syntax past 3.11, and an annotation that does not evaluate. Before
3.14 a function's annotations are evaluated as it is defined, so one that
names what it cannot be (a class's own ``int`` method, in a later method's
``-> int | None``) stops its module importing; from 3.14 they are evaluated
only when asked for, and this asks for every one."""

import ast
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path

import gamma

BACKEND = Path(__file__).resolve().parents[1]


def test_the_backend_parses_as_python_3_11():
    failed = []
    for path in [*BACKEND.glob("*.py"), *(BACKEND / "gamma").rglob("*.py"), *(BACKEND / "tests").rglob("*.py")]:
        try:
            ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 11))
        except SyntaxError as exc:
            failed.append(f"{path.relative_to(BACKEND)}:{exc.lineno}: {exc.msg}")
    assert failed == []


def test_every_module_imports_and_its_annotations_evaluate():
    annotations = importlib.import_module("annotationlib") if sys.version_info >= (3, 14) else None
    failed, seen = [], set()

    def check(obj, where):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        try:
            annotations.get_annotations(obj, format=annotations.Format.VALUE)
        except Exception as exc:  # noqa: BLE001 — each is named
            failed.append(f"{where}: {exc!r}")
        if inspect.isclass(obj):
            for name, value in vars(obj).items():
                value = getattr(value, "__func__", value)  # a classmethod's or staticmethod's function
                for f in (value.fget, value.fset) if isinstance(value, property) else (value,):
                    if inspect.isfunction(f) or inspect.isclass(f):
                        check(f, f"{where}.{name}")

    for info in pkgutil.walk_packages(gamma.__path__, "gamma."):
        module = importlib.import_module(info.name)  # before 3.14, a failing annotation fails here
        if annotations is None:
            continue
        check(module, info.name)
        for name, value in vars(module).items():
            if (inspect.isfunction(value) or inspect.isclass(value)) and value.__module__ == module.__name__:
                check(value, f"{info.name}.{name}")
    assert failed == []
