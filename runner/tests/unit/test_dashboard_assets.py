"""Asset refresh works for edits that do not change the Git revision."""

from pathlib import Path

import pytest

from atlas.presentation.http import assets


def test_uncommitted_ui_changes_get_a_new_asset_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    names = ("templates/board.html", "static/board.css", "static/board.js")
    for name in names:
        (tmp_path / name).write_text("before", encoding="utf-8")
    monkeypatch.setattr(assets, "_PRESENTATION", tmp_path)
    version = assets.board_asset_version()
    assert version == assets.board_asset_version()
    for name in names:
        (tmp_path / name).write_text("after", encoding="utf-8")
        updated = assets.board_asset_version()
        assert updated != version
        version = updated
