"""Provider-aware widths shared with embedding migrations."""

from core.embedding_dimensions import embedding_dimensions


def test_provider_default_and_explicit_width(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "voyage")
    monkeypatch.delenv("EMBED_DIMENSIONS", raising=False)
    assert embedding_dimensions() == 1024
    monkeypatch.setenv("EMBED_DIMENSIONS", "768")
    assert embedding_dimensions() == 768


def test_invalid_or_nonpositive_override_uses_provider_default(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "voyage")
    for raw in ("0", "-5", "abc", "  "):
        monkeypatch.setenv("EMBED_DIMENSIONS", raw)
        assert embedding_dimensions() == 1024


class _Conn:
    """Fake sync connection returning one pg_attribute row."""

    def __init__(self, row):
        self._row = row

    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        return self._row


def test_vector_column_width_reads_typmod():
    from core.embedding_dimensions import vector_column_width

    assert vector_column_width(_Conn((1024,)), "t") == 1024
    assert vector_column_width(_Conn((-1,)), "t") is None
    assert vector_column_width(_Conn(None), "t") is None
