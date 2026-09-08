from unittest.mock import patch
from utils.embedding_config import format_document

def test_format_document_identity():
    """Returns the raw text as a no-op if the document prefix is the bare '{text}' placeholder."""
    with patch("utils.embedding_config.get_active_profile", return_value={"document_prefix": "{text}"}):
        result = format_document("Sample text")
        assert result == "Sample text"

    # Default missing document_prefix (defaults to "{text}")
    with patch("utils.embedding_config.get_active_profile", return_value={}):
        result = format_document("Sample text", title="Ignored")
        assert result == "Sample text"

def test_format_document_with_title():
    """Formats correctly when a title is provided."""
    profile = {"document_prefix": "Title: {title}\n{text}"}
    with patch("utils.embedding_config.get_active_profile", return_value=profile):
        result = format_document("Sample text", title="My Title")
        assert result == "Title: My Title\nSample text"

def test_format_document_fallback_default_title():
    """Falls back to default_title from profile if no title is provided."""
    profile = {
        "document_prefix": "Title: {title}\n{text}",
        "default_title": "Default Title"
    }
    with patch("utils.embedding_config.get_active_profile", return_value=profile):
        result = format_document("Sample text")
        assert result == "Title: Default Title\nSample text"

def test_format_document_fallback_none():
    """Falls back to 'none' if no title and no default_title is provided."""
    profile = {"document_prefix": "Title: {title}\n{text}"}
    with patch("utils.embedding_config.get_active_profile", return_value=profile):
        result = format_document("Sample text")
        assert result == "Title: none\nSample text"

def test_format_document_no_title_placeholder():
    """Formats correctly when prefix has no {title} placeholder, ignoring the title arg."""
    profile = {"document_prefix": "Document:\n{text}"}
    with patch("utils.embedding_config.get_active_profile", return_value=profile):
        result = format_document("Sample text", title="Extra Title")
        assert result == "Document:\nSample text"
