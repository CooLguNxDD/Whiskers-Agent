"""Embedding-dimension mismatch preflight: pure comparison, doctor check, boot hook."""

import importlib.util
import pathlib

import pytest

from db_layer.embeddings import dimension_check as dc
from db_layer.embeddings.dimension_check import DimensionIssue, compare_dimensions


def _setup():
    path = pathlib.Path(__file__).resolve().parents[2] / "terminal" / "script" / "setup.py"
    spec = importlib.util.spec_from_file_location("setup_dimension_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_equal_widths_report_nothing():
    assert compare_dimensions(768, {"a": 768, "b": 768}, {"a": 768, "b": 768}) == []


def test_column_mismatch_is_reported():
    issues = compare_dimensions(768, {"memory_content_vectors": 1024}, {})
    assert issues == [
        DimensionIssue(
            collection="memory_content_vectors", configured=768, found=1024, kind="column",
            message=issues[0].message,
        )
    ]
    assert "768" in issues[0].message and "1024" in issues[0].message


def test_sample_mismatch_is_reported():
    issues = compare_dimensions(768, {"t": 768}, {"t": 1536})
    assert [(i.kind, i.found) for i in issues] == [("sample", 1536)]


def test_missing_table_or_empty_sample_is_ignored():
    assert compare_dimensions(768, {"t": None}, {"t": None}) == []
    assert compare_dimensions(768, {}, {"t": None}) == []


def test_unconstrained_column_with_bad_sample_only_reports_sample():
    issues = compare_dimensions(768, {"t": None}, {"t": 1024})
    assert [i.kind for i in issues] == ["sample"]


def test_parse_vector_width():
    assert dc._parse_vector_width("vector(1024)") == 1024
    assert dc._parse_vector_width("vector") is None
    assert dc._parse_vector_width(None) is None
    assert dc._parse_vector_width("text") is None


class _Row:
    def __init__(self, value):
        self._v = value

    def fetchone(self):
        return None if self._v is None else (self._v,)


class _FakeSession:
    """Answers the type-lookup query and the vector_dims sample query by SQL text."""

    def __init__(self, types, dims):
        self.types, self.dims, self.sql = types, dims, []

    async def execute(self, stmt, params=None):
        text = str(stmt)
        self.sql.append(text)
        assert text.lstrip().upper().startswith("SELECT")
        if "format_type" in text:
            return _Row(self.types.get(params["t"]))
        for table, width in self.dims.items():
            if table in text:
                assert "LIMIT 1" in text
                return _Row(width)
        return _Row(None)


@pytest.mark.asyncio
async def test_collect_dimension_facts_reads_widths_and_samples():
    types = {t: "vector(1024)" for t in dc.EMBEDDING_TABLES}
    types[dc.EMBEDDING_TABLES[0]] = None
    session = _FakeSession(types, {dc.EMBEDDING_TABLES[1]: 768})
    columns, sampled = await dc.collect_dimension_facts(session)
    assert columns[dc.EMBEDDING_TABLES[0]] is None
    assert columns[dc.EMBEDDING_TABLES[1]] == 1024
    assert sampled[dc.EMBEDDING_TABLES[0]] is None  # absent table is never sampled
    assert sampled[dc.EMBEDDING_TABLES[1]] == 768


# ---- doctor -----------------------------------------------------------------

def test_doctor_check_is_optional_and_mismatch_keeps_exit_code(monkeypatch, capsys):
    setup = _setup()
    assert "embedding_dimensions" in setup._OPTIONAL_CHECKS
    fake = {
        "python": {"ok": True, "detail": "3.11"},
        "embedding_dimensions": {"ok": False, "detail": "memory_content_vectors: column is 1024, configured 768"},
    }
    monkeypatch.setattr(setup, "check_prerequisites", lambda: fake)
    assert setup.run_doctor() == 0
    out = capsys.readouterr().out
    assert "warning:" in out
    assert "reindex" in out


def test_doctor_check_skips_when_db_unreachable(monkeypatch):
    setup = _setup()
    result = setup._embedding_dimensions_check(db_ok=False)
    assert result == {"ok": True, "detail": "db unreachable, skipped"}


def test_doctor_check_reports_mismatch_without_raising(monkeypatch):
    setup = _setup()
    issue = DimensionIssue("t", 768, 1024, "column", "t: column is 1024, configured 768")

    async def fake_run():
        return [issue]

    monkeypatch.setattr(dc, "run_dimension_check", fake_run)
    result = setup._embedding_dimensions_check(db_ok=True)
    assert result["ok"] is False and "1024" in result["detail"]


def test_doctor_check_degrades_when_collector_raises(monkeypatch):
    setup = _setup()

    async def boom():
        raise RuntimeError("no master key")

    monkeypatch.setattr(dc, "run_dimension_check", boom)
    result = setup._embedding_dimensions_check(db_ok=True)
    assert result["ok"] is True and "skipped" in result["detail"]


# ---- boot -------------------------------------------------------------------

class _Ctx:
    db_available = True


def test_boot_phase_is_registered_non_fatal():
    from core.bootstrap.phases import PHASES, phase_embedding_dimension_check

    phase = next(p for p in PHASES if p.run is phase_embedding_dimension_check)
    assert phase.fatal is False


@pytest.mark.asyncio
async def test_boot_hook_warns_once_per_issue(monkeypatch, caplog):
    from core.bootstrap.phases import phase_embedding_dimension_check

    issues = [
        DimensionIssue("a", 768, 1024, "column", "a mismatch"),
        DimensionIssue("b", 768, 1536, "sample", "b mismatch"),
    ]

    async def fake_run():
        return issues

    monkeypatch.setattr(dc, "run_dimension_check", fake_run)
    with caplog.at_level("WARNING", logger="whiskers"):
        await phase_embedding_dimension_check(_Ctx())
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert sum("a mismatch" in m for m in msgs) == 1
    assert sum("b mismatch" in m for m in msgs) == 1


@pytest.mark.asyncio
async def test_boot_hook_swallows_raising_collector(monkeypatch):
    from core.bootstrap.phases import phase_embedding_dimension_check

    async def boom():
        raise RuntimeError("db exploded")

    monkeypatch.setattr(dc, "run_dimension_check", boom)
    await phase_embedding_dimension_check(_Ctx())  # must not raise


@pytest.mark.asyncio
async def test_boot_hook_skips_without_db(monkeypatch):
    from core.bootstrap.phases import phase_embedding_dimension_check

    called = []

    async def fake_run():
        called.append(1)
        return []

    monkeypatch.setattr(dc, "run_dimension_check", fake_run)

    class NoDb:
        db_available = False

    await phase_embedding_dimension_check(NoDb())
    assert called == []
