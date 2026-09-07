"""scripts/repos.py: which account owns the checkouts and the cron lines.

This is the module-level USER constant, so each case reloads the script with
the environment it is testing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPOS = Path(__file__).parents[3] / "scripts" / "repos.py"


def _load(monkeypatch: pytest.MonkeyPatch, **env: str | None):
    for key in ("REPOS_USER", "SUDO_USER", "USER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if value is not None:
            monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("atlas_repos_owner", _REPOS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sudo_user_wins_over_the_root_that_sudo_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sudo repos.py apply` must still own everything as the human behind it.

    sudo sets USER=root and SUDO_USER=domdd. Reading USER there resolves to
    root, and apply() then clones with root's ~/.ssh/config -- which has none
    of the per-repo deploy-key aliases -- and renders every cron line to run
    as root with HOME=/home/root. It fails quietly: the clone error goes to a
    log and apply carries on to install a cron file that is wrong for every
    repo in the registry, including ones that were working before.
    """
    module = _load(monkeypatch, USER="root", SUDO_USER="domdd")
    assert module.USER == "domdd"


def test_explicit_override_wins_over_sudo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch, REPOS_USER="ops", SUDO_USER="domdd", USER="root")
    assert module.USER == "ops"


def test_plain_invocation_uses_the_invoking_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load(monkeypatch, USER="domdd")
    assert module.USER == "domdd"


def test_apply_refuses_to_run_as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The remaining way in is real root with no SUDO_USER (a root shell, or a
    unit running as root). Stopping before anything is written is the whole
    point: a half-applied run leaves an installed cron file behind."""
    module = _load(monkeypatch, USER="root")
    assert module.USER == "root"

    called: list[object] = []
    monkeypatch.setattr(module, "load_registry", lambda: called.append("loaded"))

    with pytest.raises(SystemExit) as exit_info:
        module.apply()

    assert "must not run as root" in str(exit_info.value)
    assert called == [], "apply must stop before it touches the registry"
