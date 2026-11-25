import sys
from importlib import import_module
from pathlib import Path

import pytest


def test_python_version() -> None:
    assert sys.version_info[:2] == (3, 12)


@pytest.mark.parametrize("package_name", ["backend", "backend.app"])
def test_project_package_import(package_name: str, pytestconfig: pytest.Config) -> None:
    package = import_module(package_name)
    package_file = pytestconfig.rootpath.joinpath(*package_name.split("."), "__init__.py")

    assert package.__file__ is not None
    assert Path(package.__file__).resolve() == package_file.resolve()


def test_pytest_project_configuration(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.inipath == pytestconfig.rootpath / "pyproject.toml"
    assert pytestconfig.getini("testpaths") == ["tests"]
    assert pytestconfig.getoption("strict_markers")

    registered_markers = {
        marker.partition(":")[0].strip() for marker in pytestconfig.getini("markers")
    }
    assert {"integration", "live"} <= registered_markers
