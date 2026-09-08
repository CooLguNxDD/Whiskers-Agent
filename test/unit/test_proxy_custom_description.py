from unittest.mock import MagicMock
from core.proxy_tools.proxy_tool_loader import collect_from_proxy

class FakeTool:
    def __init__(self, name: str, description: str, inputSchema: dict):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema

def test_collect_from_proxy_custom_description():
    tools = [
        FakeTool(name="tool1", description="This is tool 1 description.", inputSchema={"type": "object"}),
        FakeTool(name="tool2", description="This is tool 2 description.", inputSchema={"type": "object"}),
    ]
    provider = MagicMock()

    # 1. Custom description provided
    descriptors = collect_from_proxy("my_proxy", tools, provider, custom_description="SERVER CTX")
    assert len(descriptors) == 2
    for desc in descriptors:
        assert desc.description.startswith("SERVER CTX\n\n")
        assert "description." in desc.description

    # 2. Custom description is None (default)
    descriptors_default = collect_from_proxy("my_proxy", tools, provider)
    assert len(descriptors_default) == 2
    assert descriptors_default[0].description == "This is tool 1 description."
    assert descriptors_default[1].description == "This is tool 2 description."

    # 3. Custom description is empty string
    descriptors_empty = collect_from_proxy("my_proxy", tools, provider, custom_description="")
    assert len(descriptors_empty) == 2
    assert descriptors_empty[0].description == "This is tool 1 description."
    assert descriptors_empty[1].description == "This is tool 2 description."

def test_collect_from_proxy_custom_description_strip_and_cap(monkeypatch):
    tools = [
        FakeTool(name="tool1", description="Original description.", inputSchema={"type": "object"}),
    ]
    provider = MagicMock()

    # 4. Whitespace strip test
    descriptors_strip = collect_from_proxy("my_proxy", tools, provider, custom_description="  SERVER CTX WITH WHITESPACE  ")
    assert len(descriptors_strip) == 1
    assert descriptors_strip[0].description == "SERVER CTX WITH WHITESPACE\n\nOriginal description."

    # 5. Over-cap truncation test
    monkeypatch.setattr("core.proxy_tools.proxy_tool_loader._max_custom_desc_chars", lambda: 10)
    descriptors_cap = collect_from_proxy("my_proxy", tools, provider, custom_description="123456789012345")
    assert len(descriptors_cap) == 1
    assert descriptors_cap[0].description == "1234567890\n\nOriginal description."


def test_collect_from_proxy_workspace_label():
    tools = [
        FakeTool(name="tool1", description="Description 1", inputSchema={"type": "object"}),
    ]
    provider = MagicMock()

    # workspace_label provided — also stamped as workspace: tag for meta round-trip
    descriptors = collect_from_proxy("my_proxy", tools, provider, workspace_label="my-workspace")
    assert len(descriptors) == 1
    assert descriptors[0].workspace_label == "my-workspace"
    assert descriptors[0].effective_workspace_label == "my-workspace"
    assert "workspace:my-workspace" in descriptors[0].tags
    payload = descriptors[0].to_payload()
    assert payload["workspace_label"] == "my-workspace"
    assert "workspace:my-workspace" in payload["tags"]

    # workspace_label is None (default)
    descriptors_default = collect_from_proxy("my_proxy", tools, provider)
    assert len(descriptors_default) == 1
    assert descriptors_default[0].workspace_label is None
    assert descriptors_default[0].effective_workspace_label is None
    assert descriptors_default[0].to_payload()["workspace_label"] is None
    assert not any(t.startswith("workspace:") for t in descriptors_default[0].tags)

