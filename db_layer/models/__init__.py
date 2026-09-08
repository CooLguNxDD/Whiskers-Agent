"""
db_layer.models — package facade.

Split (Phase 5 modularity refactor) from a single 672-line models.py into themed submodules:
base.py, legacy.py, auth.py, embeddings.py, job_search.py, config.py, workflow.py, telemetry.py.
Re-exports every model class as a module-level attribute so existing
`from db_layer.models import X` callers across the codebase keep resolving unchanged.
"""
from db_layer.models.base import Base
from db_layer.models.legacy import OAuthClient, OAuthAccessToken, OAuthClientLegacy
from db_layer.models.auth import (
    PluginModel, AuthKeypair, OAuthClientV2, OAuthAuthCode, OAuthToken,
    PluginCredential, PluginOAuthToken, PluginOAuthPKCEState, MCPBearerToken,
    MCPPendingAuth, ApiKey, ApiKeyScopePreset, User, Tenant,
)
from db_layer.models.embeddings import (
    ContentVector,
    SearchContentVector, MemoryContentVector, RouteEmbedding,
    EmbeddingJob,
)
from db_layer.models.artifacts import ArtifactLink
from db_layer.models.config import ToolConfig, ToolPermission, ApiCache, ServerSetting, LLMPoolEntry
from db_layer.models.workflow import WorkflowPlan, WorkflowExecution, Goal, ChatSession, ChatMessage
from db_layer.models.telemetry import ToolCallEvent, RelaySessionEvent, GraphRunEvent

__all__ = [
    "Base", "OAuthClient", "OAuthAccessToken", "OAuthClientLegacy",
    "PluginModel", "AuthKeypair", "OAuthClientV2", "OAuthAuthCode", "OAuthToken",
    "PluginCredential", "PluginOAuthToken", "PluginOAuthPKCEState", "MCPBearerToken",
    "MCPPendingAuth", "ApiKey", "ApiKeyScopePreset", "User", "Tenant",
    "ContentVector", "RouteEmbedding",
    "SearchContentVector", "MemoryContentVector", "EmbeddingJob",
    "ArtifactLink",
    "ToolConfig", "ToolPermission", "ApiCache", "ServerSetting", "LLMPoolEntry",
    "WorkflowPlan", "WorkflowExecution", "Goal", "ChatSession", "ChatMessage",
    "ToolCallEvent", "RelaySessionEvent", "GraphRunEvent",
]
