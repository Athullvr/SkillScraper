from __future__ import annotations

import json

import pytest

from skillscout.project_context import ProjectContextError, read_project_context


def test_reads_allowlisted_framework_metadata_without_executing_scripts(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="sample"\nrequires-python=">=3.12"\ndependencies=["fastapi>=0.100"]\n',
        encoding="utf-8",
    )
    marker = tmp_path / "script-ran"
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "18"}, "scripts": {"postinstall": f"touch {marker}"}}),
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("lock data ignored", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "pyproject.toml").write_text("[project]", encoding="utf-8")

    context = read_project_context(tmp_path)

    assert "Dependency detected: FastAPI" in context.facts
    assert "Dependency detected: React" in context.facts
    assert "uv.lock" in context.detected_files
    assert "nested/pyproject.toml" not in context.detected_files
    assert not marker.exists()


def test_metadata_read_is_bounded(tmp_path):
    (tmp_path / "package.json").write_bytes(b"{" + b" " * (70 * 1024))

    context = read_project_context(tmp_path)

    assert any("read limit" in item for item in context.unknowns)
    assert not context.facts


def test_invalid_project_path_is_reported(tmp_path):
    with pytest.raises(ProjectContextError, match="not a directory"):
        read_project_context(tmp_path / "missing")

