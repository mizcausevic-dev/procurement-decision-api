"""Fail the release if wheel/sdist contents differ from the intended package boundary."""

from __future__ import annotations

import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path


def main() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    prefix = f"procurement_decision_api-{version}"
    wheel_path = Path("dist") / f"{prefix}-py3-none-any.whl"
    sdist_path = Path("dist") / f"{prefix}.tar.gz"
    assert set(Path("dist").iterdir()) - {Path("dist") / ".gitignore"} == {
        wheel_path,
        sdist_path,
    }, "unexpected distribution files"

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        assert "procurement_decision_api/limits.py" in names
        assert "procurement_decision_api/app.py" in names
        assert "procurement_decision_api/py.typed" in names
        assert all(
            name.startswith(("procurement_decision_api/", f"{prefix}.dist-info/"))
            and not name.endswith((".pyc", ".env"))
            for name in names
        ), "wheel contains a file outside the package and metadata"
        metadata = Parser().parsestr(wheel.read(f"{prefix}.dist-info/METADATA").decode("utf-8"))
        assert metadata["Name"] == project["name"]
        assert metadata["Version"] == version
        assert any("rfc8785==0.1.4" in value for value in metadata.get_all("Requires-Dist", []))

    with tarfile.open(sdist_path) as sdist:
        names = {member.name.removeprefix(f"{prefix}/") for member in sdist.getmembers()}
        allowed_roots = {"src", "tests", ".gitignore", "LICENSE", "README.md", "pyproject.toml", "PKG-INFO"}
        assert all(name.split("/", 1)[0] in allowed_roots for name in names), (
            "sdist contains a file outside the source allowlist"
        )
        assert all(
            name.endswith(".py") or name.endswith("/py.typed")
            for name in names
            if name.startswith(("src/", "tests/"))
        ), "sdist contains an unexpected source or test file"
        assert "src/procurement_decision_api/limits.py" in names
        assert "src/procurement_decision_api/py.typed" in names
        assert "tests/test_limits.py" in names
    print(f"Distribution contents verified for {prefix}")


if __name__ == "__main__":
    main()
