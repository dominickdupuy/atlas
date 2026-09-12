"""scripts/repos.py: which account owns the checkouts and the cron lines.

This is the module-level USER constant, so each case reloads the script with
the environment it is testing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import types
from pathlib import Path

import pytest

_REPOS = Path(__file__).parents[3] / "scripts" / "repos.py"


def _load(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> types.ModuleType:
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


def test_first_clone_creates_its_directory_as_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The checkouts live under /opt, which is root-owned, so a repo being
    cloned for the first time cannot create its own directory. Without this the
    first apply for any new repo fails with "could not create work tree dir ...
    Permission denied", and apply logs it and carries on -- so the cron line is
    installed for a checkout that does not exist."""
    module = _load(monkeypatch, USER="domdd")
    target = tmp_path / "opt" / "health"

    sudo_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(module, "sudo", lambda *a: sudo_calls.append(a))
    git_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        git_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    class _Log:
        file = None

        def __call__(self, message: str) -> None:
            pass

    module.update_checkout(
        {"path": str(target), "url": "git@github-health:x/y.git", "branch": "main"},
        _Log(),
    )

    assert sudo_calls == [
        ("install", "-d", "-o", "domdd", "-g", "domdd", "-m", "755", str(target))
    ], "the directory must be made as root and handed to the owning account"
    assert git_calls and git_calls[0][:2] == ["git", "clone"]
    assert "sudo" not in git_calls[0], (
        "the clone itself stays unprivileged: it needs the owning account's ssh "
        "config for the deploy-key alias, and must leave the tree owned by it"
    )


def test_an_existing_checkout_is_pulled_without_touching_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load(monkeypatch, USER="domdd")
    target = tmp_path / "health"
    (target / ".git").mkdir(parents=True)

    monkeypatch.setattr(module, "sudo", lambda *a: pytest.fail("no sudo for a pull"))
    calls: list[list[str]] = []
    def fake_run_pull(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run_pull)

    class _Log:
        file = None

        def __call__(self, message: str) -> None:
            pass

    module.update_checkout({"path": str(target), "url": "x", "branch": "main"}, _Log())
    assert calls[0][:3] == ["git", "-C", str(target)]
    assert "pull" in calls[0]
