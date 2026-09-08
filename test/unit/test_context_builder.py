import pytest
from unittest.mock import MagicMock
from core.context_builder import ContextBuilder

def test_context_builder_initialization():
    """Test that ContextBuilder initializes with base instructions."""
    builder = ContextBuilder("Base instruction set.")
    assert builder.instructions == ["Base instruction set."]

def test_add_context_new():
    """Test adding new, unique context."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("Additional context string.")
    assert builder.instructions == ["Base instruction set.", "Additional context string."]

def test_add_context_duplicate():
    """Test that duplicate context (including with different whitespace) is not added."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("Additional context string.")
    builder.add_context("Additional context string.") # Duplicate
    builder.add_context("  Additional context string.  ") # Duplicate with whitespace
    assert builder.instructions == ["Base instruction set.", "Additional context string."]

def test_add_context_empty():
    """Test that empty, None, or whitespace-only context is ignored."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("")
    builder.add_context(None)
    builder.add_context("   ")
    assert builder.instructions == ["Base instruction set."]

def test_add_context_strip():
    """Test that context string is stripped of leading/trailing whitespace."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("  Spaced context string.  ")
    assert builder.instructions == ["Base instruction set.", "Spaced context string."]

def test_get_instructions():
    """Test formatting instructions string."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("Second instruction.")
    builder.add_context("Third instruction.")

    expected_output = "Base instruction set.\n\nSecond instruction.\n\nThird instruction."
    assert builder.get_instructions() == expected_output

def test_update_mcp_instructions():
    """Test updating the instructions property on a mock FastMCP object."""
    builder = ContextBuilder("Base instruction set.")
    builder.add_context("Second instruction.")

    mock_mcp_app = MagicMock()
    mock_mcp_app.instructions = None

    builder.update_mcp_instructions(mock_mcp_app)

    expected_output = "Base instruction set.\n\nSecond instruction."
    assert mock_mcp_app.instructions == expected_output
