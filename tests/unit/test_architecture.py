"""Enforces the clean-architecture dependency rule: imports may only point inward.

    entrypoints -> adapters -> application -> domain

domain and application must also stay free of I/O and framework libraries.
"""

import ast
import pathlib

import pytest

PKG = pathlib.Path(__file__).parents[2] / "src" / "orisun_iwadi"
LAYERS = ["domain", "application", "adapters", "entrypoints"]  # inner -> outer
FRAMEWORKS = {"dagster", "boto3", "botocore", "psycopg", "requests", "urllib3", "fastapi",
              "sqlalchemy"}


def imports_of(path: pathlib.Path) -> set[str]:
    """Fully qualified module names imported by a file (relative imports resolved)."""
    module = ".".join(path.relative_to(PKG.parent).with_suffix("").parts)
    package = module.rsplit(".", 1)[0] if path.name != "__init__.py" else module
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                found.add(".".join(base + ([node.module] if node.module else [])))
            else:
                found.add(node.module)
    return found


def layer_files(layer):
    return sorted((PKG / layer).rglob("*.py"))


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_only_imports_inner_layers(layer):
    outer = LAYERS[LAYERS.index(layer) + 1:]
    for f in layer_files(layer):
        for mod in imports_of(f):
            for o in outer:
                assert not mod.startswith(f"orisun_iwadi.{o}"), f"{f.name} imports {mod}"


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_core_has_no_framework_or_io_imports(layer):
    for f in layer_files(layer):
        for mod in imports_of(f):
            assert mod.split(".")[0] not in FRAMEWORKS, f"{f.name} imports {mod}"
