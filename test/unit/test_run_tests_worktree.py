"""run_tests.py runs pytest in a throwaway container mounting the worktree when invoked from a git worktree."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_tests_worktree_script", Path(__file__).resolve().parents[2] / "scripts" / "run_tests.py"
)
run_tests = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_tests)


def test_is_git_worktree_true_for_gitdir_file(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /repo/.git/worktrees/x\n", encoding="utf-8")
    assert run_tests.is_git_worktree(tmp_path) is True


def test_is_git_worktree_false_for_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    assert run_tests.is_git_worktree(tmp_path) is False


def test_is_git_worktree_false_when_missing(tmp_path):
    assert run_tests.is_git_worktree(tmp_path) is False


def test_is_git_worktree_false_for_file_without_gitdir(tmp_path):
    (tmp_path / ".git").write_text("something else", encoding="utf-8")
    assert run_tests.is_git_worktree(tmp_path) is False


def test_build_cmd_mounts_worktree_and_forwards_args(tmp_path):
    cmd = run_tests.build_worktree_docker_cmd(tmp_path, "img:1", ["test/unit/a.py", "-k", "foo"], {})
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "-v" in cmd and f"{tmp_path}:/app" in cmd
    assert cmd[cmd.index("-w") + 1] == "/app"
    assert "PYTHONPATH=/app" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"
    tail = cmd[cmd.index("img:1") + 1:]
    assert tail == ["python", "scripts/run_tests.py", "test/unit/a.py", "-k", "foo"]


def test_build_cmd_image_from_env(tmp_path):
    cmd = run_tests.build_worktree_docker_cmd(tmp_path, None, [], {"WHISKERS_TEST_IMAGE": "custom:tag"})
    assert "custom:tag" in cmd


def test_build_cmd_default_image(tmp_path):
    cmd = run_tests.build_worktree_docker_cmd(tmp_path, None, [], {})
    assert "opencattunnelproject-whiskers-agent" in cmd


def test_build_cmd_network_override(tmp_path):
    cmd = run_tests.build_worktree_docker_cmd(tmp_path, "i", [], {"WHISKERS_TESTS_NETWORK": "mynet"})
    assert cmd[cmd.index("--network") + 1] == "mynet"


def _run_main(monkeypatch, root_has_gitfile, tmp_path):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout="whiskers-agent-server\n", stderr="")

    monkeypatch.setattr(run_tests, "is_docker_container", lambda: False)
    monkeypatch.setattr(run_tests, "is_git_worktree", lambda root: root_has_gitfile)
    monkeypatch.setattr(run_tests.subprocess, "run", fake_run)
    monkeypatch.setattr(run_tests.sys, "argv", ["run_tests.py", "test/unit/x.py"])
    monkeypatch.delenv("WHISKERS_TEST_IMAGE", raising=False)
    monkeypatch.delenv("WHISKERS_TESTS_NETWORK", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_tests.main()
    assert exc.value.code == 0
    return calls


def test_main_worktree_uses_docker_run(monkeypatch, tmp_path, capsys):
    calls = _run_main(monkeypatch, True, tmp_path)
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[-3:] == ["python", "scripts/run_tests.py", "test/unit/x.py"] or "test/unit/x.py" in cmd
    assert kw["env"]["MSYS_NO_PATHCONV"] == "1"
    assert "worktree" in capsys.readouterr().out.lower()


def test_main_non_worktree_uses_docker_exec(monkeypatch, tmp_path):
    calls = _run_main(monkeypatch, False, tmp_path)
    exec_cmds = [c for c, _ in calls if c[:2] == ["docker", "exec"]]
    assert len(exec_cmds) == 1
    assert not any(c[:2] == ["docker", "run"] for c, _ in calls)
