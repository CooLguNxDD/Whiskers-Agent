"""Provider-aware widths shared with embedding migrations."""

from core.embedding_dimensions import embedding_dimensions


def test_provider_default_and_explicit_width(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "voyage")
    monkeypatch.delenv("EMBED_DIMENSIONS", raising=False)
    assert embedding_dimensions() == 1024
    monkeypatch.setenv("EMBED_DIMENSIONS", "768")
    assert embedding_dimensions() == 768
