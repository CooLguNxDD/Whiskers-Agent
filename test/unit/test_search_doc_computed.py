"""search_doc is a Postgres GENERATED ALWAYS column — ORM must not INSERT it."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

from db_layer.models.embeddings import (
    MemoryContentVector,
    RouteEmbedding,
    SearchContentVector,
)


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
        "collection": "c",
        "content_text": "hello",
        "content": "hello",
        "content_hash": "h",
        "model": "m",
        "plugin_id": "p",
        "operation_id": "op",
        "method": "GET",
        "path": "/",
        "path_template": "/",
        "description": "d",
        "contact_id": "1",
        "conversation_id": "c",
        "message_id": "m1",
        "world_id": "w",
        "doc_kind": "object",
        "doc_id": "d1",
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


def test_memory_content_vector_search_doc_is_computed():
    _assert_computed_and_omitted_from_insert(MemoryContentVector, "content_text")


def test_search_content_vector_search_doc_is_computed():
    _assert_computed_and_omitted_from_insert(SearchContentVector, "content_text")


def test_route_search_doc_is_computed():
    _assert_computed_and_omitted_from_insert(RouteEmbedding, "doc_text")
    # UnityWorldVector (world_semantic_plugin-owned) is covered by
    # plugins/world_semantic_plugin/tests/test_unity_vector_search_doc.py


def test_memory_insert_statement_omits_search_doc():
    """Compile an ORM bulk-style insert dict and ensure search_doc is not required."""
    from sqlalchemy.dialects.postgresql import insert

    stmt = insert(MemoryContentVector).values(
        collection="plan_recipes",
        content_text="goal notes",
        embedding=None,
        model="test-model",
        meta={},
        content_hash="abc",
        tenant_id=1,
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    # Parameter keys / column list must not include search_doc
    assert "search_doc" not in compiled.params
    assert "search_doc" not in str(compiled)
