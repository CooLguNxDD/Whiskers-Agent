"""run_tests.py appends failing-test reporting flags unless the caller chose their own."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_tests_script", Path(__file__).resolve().parents[2] / "scripts" / "run_tests.py"
)
run_tests = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_tests)


def test_adds_rfE_and_short_tb_by_default():
    assert run_tests.with_failure_report(["test/unit/x.py"]) == [
        "test/unit/x.py", "-rfE", "--tb=short",
    ]


def test_respects_caller_report_chars():
    args = ["-rA", "test/unit/x.py"]
    out = run_tests.with_failure_report(args)
    assert "-rfE" not in out
    assert "--tb=short" in out


def test_respects_caller_tb_style():
    out = run_tests.with_failure_report(["--tb=long", "-k", "foo"])
    assert out.count("--tb=long") == 1
    assert not any(a.startswith("--tb=") and a != "--tb=long" for a in out)
    assert "-rfE" in out


def test_does_not_mutate_input():
    args = ["a"]
    run_tests.with_failure_report(args)
    assert args == ["a"]
