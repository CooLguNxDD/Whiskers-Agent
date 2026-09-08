"""
Unit tests for the Gemini Vertex AI provider.
"""

import os
import sys
from types import ModuleType
import pytest
from core.llm_provider_management import LLMProvider, _PROVIDER_ENV_KEY, _make_llm, make_embeddings


def test_vertex_provider_properties():
    """Assert that LLMProvider enum and env key map match expectations."""
    # 1. Assert LLMProvider("gemini-vertex").value == "gemini-vertex"
    assert LLMProvider("gemini-vertex") == LLMProvider.GEMINI_VERTEX
    assert LLMProvider.GEMINI_VERTEX.value == "gemini-vertex"

    # 2. Assert _PROVIDER_ENV_KEY[LLMProvider.GEMINI_VERTEX] == "GOOGLE_VERTEX_API_KEY"
    assert _PROVIDER_ENV_KEY[LLMProvider.GEMINI_VERTEX] == "GOOGLE_VERTEX_API_KEY"


def test_vertex_provider_make_llm(monkeypatch):
    """Test that _make_llm properly invokes ChatGoogleGenerativeAI with correct parameters."""
    captured_env = {}

    class FakeChatGoogleGenerativeAI:
        """Fake ChatGoogleGenerativeAI capturing constructor kwargs + env snapshot at creation (for leak test)."""
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            # Snapshot relevant keys at the moment of construction (inside isolated env)
            captured_env["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            captured_env["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY")

    import os as _os  # ensure we can monkey env

    # Define a stub module exposing the fake class
    stub_module = ModuleType("langchain_google_genai")
    stub_module.ChatGoogleGenerativeAI = FakeChatGoogleGenerativeAI

    # Patch langchain_google_genai via sys.modules for lazy import
    monkeypatch.setitem(sys.modules, "langchain_google_genai", stub_module)

    # Set conflicting plain Gemini keys to verify isolation during vertex construction
    monkeypatch.setenv("GOOGLE_API_KEY", "plain-gemini-key")
    monkeypatch.setenv("GEMINI_API_KEY", "another-plain-key")

    # 3. Call _make_llm and assert fake ChatGoogleGenerativeAI received correct parameters
    chat_llm = _make_llm(LLMProvider.GEMINI_VERTEX, "gemini-2.5-flash", api_key="k")
    assert isinstance(chat_llm, FakeChatGoogleGenerativeAI)
    assert chat_llm.kwargs.get("model") == "gemini-2.5-flash"
    assert chat_llm.kwargs.get("vertexai") is True
    assert chat_llm.kwargs.get("google_api_key") == "k"

    # Verify that the forbidden keys were not visible during the ChatGoogleGenerativeAI(...) call
    assert captured_env.get("GOOGLE_API_KEY") is None
    assert captured_env.get("GEMINI_API_KEY") is None

    # After return, the real process env should still have the original values (restored)
    assert _os.environ.get("GOOGLE_API_KEY") == "plain-gemini-key"
    assert _os.environ.get("GEMINI_API_KEY") == "another-plain-key"


def test_vertex_provider_make_embeddings(monkeypatch):
    """Test that make_embeddings properly invokes GoogleGenerativeAIEmbeddings with correct parameters."""
    captured_env = {}

    class FakeGoogleGenerativeAIEmbeddings:
        """Fake GoogleGenerativeAIEmbeddings capturing constructor kwargs + env at creation time."""
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            captured_env["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            captured_env["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY")

    # Define a stub module exposing the fake class
    stub_module = ModuleType("langchain_google_genai")
    stub_module.GoogleGenerativeAIEmbeddings = FakeGoogleGenerativeAIEmbeddings

    # Patch langchain_google_genai via sys.modules for lazy import
    monkeypatch.setitem(sys.modules, "langchain_google_genai", stub_module)

    # Set conflicting keys to prove isolation for vertex embeddings
    monkeypatch.setenv("GOOGLE_API_KEY", "plain-gemini-key-embed")
    monkeypatch.setenv("GEMINI_API_KEY", "another-plain-key-embed")

    # 4. Call make_embeddings and assert subclass of FakeGoogleGenerativeAIEmbeddings received correct parameters
    embeddings = make_embeddings("gemini-vertex", "text-embedding-005", 768, api_key="k")
    assert isinstance(embeddings, FakeGoogleGenerativeAIEmbeddings)
    assert embeddings.kwargs.get("model") == "text-embedding-005"
    assert embeddings.kwargs.get("vertexai") is True
    assert embeddings.kwargs.get("google_api_key") == "k"

    # Env keys for plain gemini must have been stripped during construction
    assert captured_env.get("GOOGLE_API_KEY") is None
    assert captured_env.get("GEMINI_API_KEY") is None

    # Original env restored after call
    assert os.environ.get("GOOGLE_API_KEY") == "plain-gemini-key-embed"
    assert os.environ.get("GEMINI_API_KEY") == "another-plain-key-embed"
