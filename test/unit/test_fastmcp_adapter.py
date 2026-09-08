import pytest
from unittest.mock import MagicMock
from core.proxy_tools.fastmcp_adapter import (
    iter_tools,
    tool_description,
    tool_input_schema,
    tool_tags,
    tool_callable,
    tool_name,
    disable_tool,
    enable_tool,
)


def test_iter_tools_3_x():
    mcp_app = MagicMock()
    mock_provider = MagicMock()
    tool_obj = MagicMock()
    other_obj = MagicMock()
    mock_provider._components = {
        "tool:foo": tool_obj,
        "other:bar": other_obj,
    }
    mcp_app._local_provider = mock_provider
    mcp_app._tool_manager = None
    
    tools = iter_tools(mcp_app)
    assert tools == [tool_obj]


def test_iter_tools_legacy_2_x_dict():
    mcp_app = MagicMock()
    mcp_app._local_provider = None
    tool_mgr = MagicMock()
    tool_obj = MagicMock()
    tool_mgr._tools = {"foo": tool_obj}
    mcp_app._tool_manager = tool_mgr
    
    tools = iter_tools(mcp_app)
    assert tools == [tool_obj]


def test_iter_tools_legacy_2_x_list():
    mcp_app = MagicMock()
    mcp_app._local_provider = None
    tool_mgr = MagicMock()
    tool_obj = MagicMock()
    # List option
    tool_mgr.list_tools = lambda: [tool_obj]
    tool_mgr._tools = None
    mcp_app._tool_manager = tool_mgr
    
    tools = iter_tools(mcp_app)
    assert tools == [tool_obj]


def test_disable_tool_calls_mcp_app():
    mcp_app = MagicMock()
    disable_tool(mcp_app, {"x"}, {"tool"})
    mcp_app.disable.assert_called_once_with(names={"x"}, components={"tool"})


def test_enable_tool_calls_mcp_app():
    mcp_app = MagicMock()
    enable_tool(mcp_app, {"y"}, {"tool"})
    mcp_app.enable.assert_called_once_with(names={"y"}, components={"tool"})


def test_tool_helpers():
    # Test description
    tool = MagicMock()
    tool.description = "desc"
    assert tool_description(tool) == "desc"

    tool2 = MagicMock()
    tool2.description = None
    tool2.title = "title"
    assert tool_description(tool2) == "title"

    def my_fn():
        """Docstring here"""
        pass
    tool3 = MagicMock()
    tool3.description = None
    tool3.title = None
    assert tool_description(tool3, fn=my_fn) == "Docstring here"

    # Test tool_input_schema
    tool4 = MagicMock()
    tool4.parameters = {"param": "val"}
    assert tool_input_schema(tool4) == {"param": "val"}

    tool5 = MagicMock()
    tool5.parameters = MagicMock()
    tool5.parameters.model_json_schema = lambda: {"json": "schema"}
    assert tool_input_schema(tool5) == {"json": "schema"}

    # Test tool_tags
    tool6 = MagicMock()
    tool6.tags = ["tag1", "tag2"]
    assert tool_tags(tool6) == ("tag1", "tag2")

    # Test tool_callable
    tool7 = MagicMock()
    def my_handler(): pass
    tool7.fn = my_handler
    assert tool_callable(tool7) == my_handler

    # Test tool_name
    tool8 = MagicMock()
    tool8.name = "my_name"
    assert tool_name(tool8) == "my_name"
