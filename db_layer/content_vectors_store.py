"""Back-compat shim — search engine store lives in search_content_vectors_store.

New code should import from ``db_layer.search_content_vectors_store`` (search)
or ``db_layer.memory_content_vectors_store`` (memory / harness).
"""

from db_layer.search_content_vectors_store import (  # noqa: F401
    add_content_vector,
    add_search_content_vector,
    delete_content_vector,
    delete_search_content_vector,
    list_content_vectors,
    list_search_content_vectors,
    search_content_vectors,
    search_search_content_vectors,
)

__all__ = [
    "add_content_vector",
    "add_search_content_vector",
    "delete_content_vector",
    "delete_search_content_vector",
    "list_content_vectors",
    "list_search_content_vectors",
    "search_content_vectors",
    "search_search_content_vectors",
]
