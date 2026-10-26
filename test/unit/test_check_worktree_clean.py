"""Tests for scripts/check_worktree_clean.py against throwaway git repos (no network)."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_worktree_clean.py"


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    """Override the conftest autouse fixture: this test is stdlib-only and must not import core_graph."""
    yield


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "app.py", "print(1)\n")
    _write(tmp_path, "package-lock.json", "{}\n")
    _write(tmp_path, "Packages/packages-lock.json", "{}\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _run(repo: Path, *args: str, base: str = "main") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base", base, *args],
        cwd=repo, capture_output=True, text=True,
    )


def test_clean_passes(repo):
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "OK"


def test_committed_source_change_passes(repo):
    _git(repo, "checkout", "-q", "-b", "step")
    _write(repo, "app.py", "print(2)\n")
    _git(repo, "commit", "-qam", "edit")
    assert _run(repo).returncode == 0


@pytest.mark.parametrize("lock", ["package-lock.json", "Packages/packages-lock.json"])
def test_lockfile_flagged(repo, lock):
    _write(repo, lock, '{"changed": true}\n')
    r = _run(repo)
    assert r.returncode == 1
    assert lock in r.stdout
    assert "lockfile" in r.stdout


def test_lockfile_allowed_with_allow_lock(repo):
    _write(repo, "package-lock.json", '{"changed": true}\n')
    r = _run(repo, "--allow-lock", "package-lock.json")
    assert r.returncode == 0, r.stdout + r.stderr
    # an unrelated lockfile stays flagged
    _write(repo, "Packages/packages-lock.json", '{"changed": true}\n')
    r = _run(repo, "--allow-lock", "package-lock.json")
    assert r.returncode == 1
    assert "Packages/packages-lock.json" in r.stdout


def test_committed_lockfile_change_flagged(repo):
    _git(repo, "checkout", "-q", "-b", "step")
    _write(repo, "Packages/packages-lock.json", '{"dropped": true}\n')
    _git(repo, "commit", "-qam", "drop cozy")
    r = _run(repo)
    assert r.returncode == 1
    assert "Packages/packages-lock.json" in r.stdout


def test_stray_claude_skill_flagged(repo):
    _write(repo, ".claude/skills/x/SKILL.md")
    r = _run(repo, "--allow", ".claude/*")  # --allow never excuses stray paths
    assert r.returncode == 1
    assert ".claude/skills/x/SKILL.md" in r.stdout


def test_generated_files_flagged(repo):
    _write(repo, "pkg/__pycache__/m.cpython-311.pyc")
    _write(repo, "run.log")
    _write(repo, ".env")
    _write(repo, ".pytest_cache/v/cache/x")
    r = _run(repo, "--allow", "*")
    assert r.returncode == 1
    for p in ("pkg/__pycache__/m.cpython-311.pyc", "run.log", ".env", ".pytest_cache/v/cache/x"):
        assert p in r.stdout


def test_untracked_needs_allow_glob(repo):
    _write(repo, "scripts/new_tool.py")
    r = _run(repo)
    assert r.returncode == 1
    assert "scripts/new_tool.py" in r.stdout
    assert _run(repo, "--allow", "scripts/*.py").returncode == 0
    assert _run(repo, "--allow", "scripts/").returncode == 0
    assert _run(repo, "--allow", "docs/*").returncode == 1


def test_bad_base_errors(repo):
    assert _run(repo, base="no-such-rev").returncode == 2


def test_lockfile_compared_to_requested_base_not_merge_base(repo):
    _git(repo, "checkout", "-q", "-b", "run")
    _write(repo, "package-lock.json", '{"run": true}\n')
    _git(repo, "commit", "-qam", "run lock change")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "step")
    _write(repo, "app.py", "print(2)\n")
    _git(repo, "commit", "-qam", "step change")
    r = _run(repo, base="run")
    assert r.returncode == 1
    assert "package-lock.json" in r.stdout


def test_untracked_stray_allowed_lockfile_remains_offender(repo):
    _write(repo, ".claude/package-lock.json")
    r = _run(repo, "--allow-lock", ".claude/package-lock.json")
    assert r.returncode == 1
    assert ".claude/package-lock.json" in r.stdout
    assert "stray/generated" in r.stdout


def test_untracked_lockfile_requires_allow_and_allow_lock(repo):
    _write(repo, "new/package-lock.json")
    assert _run(repo, "--allow-lock", "new/package-lock.json").returncode == 1
    assert _run(repo, "--allow", "new/*").returncode == 1
    assert _run(repo, "--allow-lock", "new/package-lock.json", "--allow", "new/*").returncode == 0


def test_git_mv_lockfile_is_reported_without_allow_lock(repo):
    _git(repo, "mv", "package-lock.json", "ordinary.json")
    r = _run(repo)
    assert r.returncode == 1
    assert "package-lock.json" in r.stdout
