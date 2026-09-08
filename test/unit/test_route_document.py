import pytest

from db_layer.embeddings.route_document import build_route_document, DOC_SCHEMA_VERSION
from core.route_registry.route_descriptor import RouteDescriptor


def test_doc_schema_version():
    assert DOC_SCHEMA_VERSION == 1


def test_build_route_document_full_fields():
    doc = build_route_document(
        plugin_id="notion",
        operation_id="proxy_notion_teamA__search_pages",
        method="POST",
        path="/pages/search",
        description="Search Notion pages in workspace.",
        tags=["notion", "proxy", "workspace:team-a"],
        workspace_label="Team A",
        param_names=["query", "filter", "page_size"],
    )

    expected = (
        "[plugin: notion · workspace: Team A]\n"
        "operation: proxy_notion_teamA__search_pages (search pages)\n"
        "POST /pages/search\n"
        "Search Notion pages in workspace.\n"
        "params: query, filter, page_size\n"
        "tags: notion, proxy, workspace:team-a"
    )
    assert doc == expected


def test_build_route_document_workspace_omitted():
    doc = build_route_document(
        plugin_id="notion",
        operation_id="search_pages",
        method="POST",
        path="/pages/search",
        description="Search Notion pages.",
        tags=["notion"],
        workspace_label=None,
        param_names=["query"],
    )

    assert "[plugin: notion]" in doc
    assert "· workspace:" not in doc


def test_build_route_document_no_params():
    doc = build_route_document(
        plugin_id="core",
        operation_id="get_status",
        method="GET",
        path="/status",
        description="Get server status.",
        tags=["core"],
        workspace_label=None,
        param_names=None,
    )

    assert "params:" not in doc


def test_build_route_document_no_tags():
    doc = build_route_document(
        plugin_id="core",
        operation_id="get_status",
        method="GET",
        path="/status",
        description="Get server status.",
        tags=None,
        workspace_label=None,
        param_names=["verbose"],
    )

    assert "tags:" not in doc


def test_build_route_document_description_unmutated():
    long_desc = "Line 1: Detailed description with special chars !@#$%^&*().\nLine 2: Unchanged formatting."
    doc = build_route_document(
        plugin_id="test",
        operation_id="test_op",
        method="GET",
        path="/test",
        description=long_desc,
    )

    assert long_desc in doc


def test_route_descriptor_to_payload_enriched():
    rd = RouteDescriptor(
        plugin_id="notion",
        operation_id="proxy_notion_teamA__search_pages",
        description="Search Notion pages.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filter": {"type": "string"},
            },
        },
        method="POST",
        path_template="/pages/search",
        tags=("notion", "workspace:Team A"),
        workspace_label="Team A",
    )

    payload = rd.to_payload()
    assert payload["plugin_id"] == "notion"
    assert payload["operation_id"] == "proxy_notion_teamA__search_pages"
    assert payload["workspace_label"] == "Team A"
    assert payload["param_names"] == ["query", "filter"]
