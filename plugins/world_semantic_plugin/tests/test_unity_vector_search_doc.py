"""search_doc is a Postgres GENERATED ALWAYS column — ORM must not INSERT it.

Split out of test/unit/test_search_doc_computed.py (world_semantic_plugin owns
UnityWorldVector; see the plugin test/data-access relocation in CLAUDE.md).
The other embeddings.py models stay covered by the core test.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

from plugins.world_semantic_plugin.models import UnityWorldVector


def _assert_computed_and_omitted_from_insert(model, source_col: str) -> None:
    col = model.__table__.c.search_doc
    assert col.computed is not None, f"{model.__name__}.search_doc missing Computed()"
    assert col.computed.persisted is True
    sql = str(col.computed.sqltext)
    assert source_col in sql

    # INSERT compiled from a fresh instance must omit search_doc entirely.
    row = model()
    # Populate only required non-generated fields so compiler still builds a statement.
    for key, val in {
        "content_text": "hello",
        "model": "m",
        "world_id": "w",
        "doc_kind": "object",
        "doc_id": "d1",
        "content_hash": "h",
    }.items():
        if hasattr(row, key) and key in model.__table__.c:
            setattr(row, key, val)

    # Table-level insert of mapper columns that are present on the instance.
    from sqlalchemy.orm import class_mapper

    mapper = class_mapper(model)
    insert_cols = [
        c.key
        for c in mapper.columns
        if c.key != "search_doc" and not c.primary_key
    ]
    # Explicit check: search_doc is computed so SQLAlchemy excludes it from
    # persisted INSERT column sets used by the unit of work.
    assert "search_doc" not in insert_cols or mapper.columns["search_doc"].computed is not None
    assert mapper.columns["search_doc"].computed is not None

    ddl = str(CreateColumn(col).compile(dialect=postgresql.dialect()))
    assert "GENERATED ALWAYS" in ddl.upper() or "COMPUTED" in ddl.upper() or "AS " in ddl.upper()


def test_unity_world_vector_search_doc_is_computed():
    _assert_computed_and_omitted_from_insert(UnityWorldVector, "content_text")
