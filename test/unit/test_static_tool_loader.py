import pytest
from unittest.mock import MagicMock
from core.proxy_tools.static_tool_loader import collect_from

def _setup_mock_mcp(mock_tools, provider_type="3.x"):
    mcp_app = MagicMock()
    if provider_type == "3.x":
        mock_provider = MagicMock()
        mock_provider._components = {f"tool:{name}": tool for name, tool in mock_tools.items()}
        mcp_app._local_provider = mock_provider
    else:
        mcp_app._local_provider = None
        tool_mgr = MagicMock()
        tool_mgr._tools = mock_tools
        mcp_app._tool_manager = tool_mgr
    return mcp_app

def test_collect_returns_descriptors_for_matching_tag():
    mock_tool = MagicMock()
    mock_tool.name = "list_records"
    mock_tool.description = "List records"
    mock_tool.tags = {"p1"}
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool})
    
    descriptors = collect_from("p1", [], mcp_app=mcp_app)
    assert len(descriptors) == 1
    assert descriptors[0].operation_id == "p1__list_records"

def test_collect_qualifies_operation_id():
    mock_tool = MagicMock()
    mock_tool.name = "list_records"
    mock_tool.tags = {"my_plugin"}
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool})
    
    descriptors = collect_from("my_plugin", [], mcp_app=mcp_app)
    assert descriptors[0].operation_id == "my_plugin__list_records"

def test_collect_no_double_qualify():
    mock_tool = MagicMock()
    mock_tool.name = "my_plugin__list_records"
    mock_tool.tags = {"my_plugin"}
    
    mcp_app = _setup_mock_mcp({"my_plugin__list_records": mock_tool})
    
    descriptors = collect_from("my_plugin", [], mcp_app=mcp_app)
    assert descriptors[0].operation_id == "my_plugin__list_records"

def test_collect_sets_is_fast_path_when_fn_present():
    def my_fn(): pass
    
    mock_tool = MagicMock()
    mock_tool.name = "list_records"
    mock_tool.fn = my_fn
    mock_tool.tags = {"p1"}
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool})
    
    descriptors = collect_from("p1", [], mcp_app=mcp_app)
    assert descriptors[0].is_fast_path is True
    assert descriptors[0].callable_ref == my_fn

def test_collect_deduplicates_same_name():
    mock_tool1 = MagicMock()
    mock_tool1.name = "list_records"
    mock_tool1.tags = {"p1"}
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool1})
    
    descriptors = collect_from("p1", [], mcp_app=mcp_app)
    assert len(descriptors) == 1

def test_collect_logs_warning_when_empty(caplog):
    mcp_app = _setup_mock_mcp({})
    
    collect_from("p1", [], mcp_app=mcp_app)
    assert "collected 0 routes" in caplog.text

def test_collect_uses_description_attribute():
    mock_tool = MagicMock()
    mock_tool.name = "list_records"
    mock_tool.description = "Custom desc"
    mock_tool.tags = {"p1"}
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool})
    
    descriptors = collect_from("p1", [], mcp_app=mcp_app)
    assert descriptors[0].to_payload()["description"] == "Custom desc"

def test_collect_falls_back_to_docstring():
    mock_tool = MagicMock()
    mock_tool.name = "list_records"
    mock_tool.description = None
    mock_tool.title = None
    mock_tool.tags = {"p1"}
    
    def my_fn():
        """Docstring desc"""
        pass
    mock_tool.fn = my_fn
    
    mcp_app = _setup_mock_mcp({"list_records": mock_tool})
    
    descriptors = collect_from("p1", [], mcp_app=mcp_app)
    assert descriptors[0].to_payload()["description"] == "Docstring desc"

