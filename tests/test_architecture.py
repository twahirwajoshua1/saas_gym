"""
Checks on how the code is organised.

Screens (views and forms) show things and collect input. They must not change
the database themselves; they call a function in `services.py` instead. These
tests read the source code of every screen file and fail if one breaks the rule.

The rules about which module may import which are checked separately by the
`lint-imports` command (see pyproject.toml).
"""

import ast
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parent.parent / "apps"
SCREEN_FILES = sorted(p for name in ("views.py", "forms.py") for p in APPS_DIR.glob(f"*/{name}"))

# Method names that write to the database.
WRITING_METHODS = {
    "save",
    "create",
    "delete",
    "update",
    "get_or_create",
    "update_or_create",
    "bulk_create",
    "bulk_update",
}


def test_there_are_screen_files_to_check():
    assert len(SCREEN_FILES) >= 4


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_screens_do_not_import_the_database_layer(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported = [node.module or ""]
        elif isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        else:
            continue
        for name in imported:
            assert not (name == "django.db" or name.startswith("django.db.")), (
                f"{path.parent.name}/{path.name} line {node.lineno} imports {name}. "
                "Screens must not use the database directly; put the logic in services.py."
            )


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_screens_do_not_write_to_the_database(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in WRITING_METHODS, (
                f"{path.parent.name}/{path.name} line {node.lineno} calls .{node.func.attr}(). "
                "Screens must not change data themselves; call a function in services.py."
            )
