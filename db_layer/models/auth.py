# ---------------------------------------------------------------------------
# PG-native encrypted schema (core_001…core_010, core_011, core_012)
#
# BYTEA columns that hold pgp_sym_encrypt() output are declared as LargeBinary.
# Never read those columns directly — always project through
# pgp_sym_decrypt(col, current_setting('app.master_key'))::TEXT in raw SQL or
# a service-layer helper (VaultService, OAuthService).
# ---------------------------------------------------------------------------
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    JSON,
    LargeBinary,
    String,
    Text,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, UUID
from db_layer.models.base import Base


class PluginModel(Base):
    """DB record for a registered plugin (plugins table from core_001)."""

    __tablename__ = "plugins"

    id = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    version = Column(String, nullable=False)
    capabilities = Column(ARRAY(TEXT), nullable=False)
    required_credentials = Column(ARRAY(TEXT), nullable=False)
    external_oauth_providers = Column(ARRAY(TEXT), nullable=False)
    meta = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    registered_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    # Source-tree fingerprint (core_037); column is SoT, not meta JSONB
    content_hash = Column(String, nullable=True)


class AuthKeypair(Base):
    """RS256 keypair used to sign JWTs (auth_keypairs table from core_002)."""

    __tablename__ = "auth_keypairs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    kid = Column(String, nullable=False, unique=True)
    # pgp_sym_encrypt(PEM private key, master_key) → BYTEA
    private_key = Column(LargeBinary, nullable=False)
    # Plaintext PEM public key (safe to store unencrypted)
    public_key = Column(Text, nullable=False)
    algorithm = Column(String(16), nullable=False, server_default="RS256")
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=True)


class OAuthClientV2(Base):
    """
    MCP OAuth client record (oauth_clients table, updated by core_011 to add
    the ``client_data`` JSONB column).

    Replaces the legacy OAuthClient model.  The ``client_secret`` column holds
    pgp_sym_encrypt(plaintext_secret, master_key); decrypt in OAuthService.
    """

    __tablename__ = "oauth_clients"
    __table_args__ = {"extend_existing": True}  # shares table with legacy OAuthClient

    # unique id
    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))

    client_id = Column(String, primary_key=True, nullable=False, unique=True)
    # Full OAuthClientInformationFull serialised as JSON (legacy column preserved)
    client_data = Column(JSON, nullable=False)
    # pgp_sym_encrypt(secret, master_key) → BYTEA — NULL for public clients
    client_secret = Column(LargeBinary, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class OAuthAuthCode(Base):
    """Short-lived authorization codes (oauth_auth_codes from core_003)."""

    __tablename__ = "oauth_auth_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    code = Column(String, nullable=False, unique=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    redirect_uri = Column(Text, nullable=False)
    scopes_granted = Column(Text, nullable=False, server_default="")
    # S256 code_challenge (hashed; verifier never stored)
    code_challenge = Column(String, nullable=False)
    code_challenge_method = Column(String, nullable=False, server_default="S256")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class OAuthToken(Base):
    """Issued JWT token records — JTI-based revocation list (oauth_tokens from core_004)."""

    __tablename__ = "oauth_tokens"

    jti = Column(UUID(as_uuid=True), primary_key=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    token_type = Column(String(16), nullable=False, server_default="access")
    scopes = Column(Text, nullable=False, server_default="")
    issued_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class PluginCredential(Base):
    """pgcrypto-encrypted static credentials per plugin (plugin_credentials from core_006)."""

    __tablename__ = "plugin_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    plugin_id = Column(String, ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    key_name = Column(String, nullable=False)
    # pgp_sym_encrypt(plaintext_value, master_key) → BYTEA
    value = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class PluginOAuthToken(Base):
    """Encrypted OAuth tokens per plugin + provider (plugin_oauth_tokens from core_007)."""

    __tablename__ = "plugin_oauth_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    plugin_id = Column(String, ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    # pgp_sym_encrypt(access_token, master_key)
    access_token = Column(LargeBinary, nullable=False)
    # pgp_sym_encrypt(refresh_token, master_key)
    refresh_token = Column(LargeBinary, nullable=True)
    scopes = Column(Text, nullable=False, server_default="")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class PluginOAuthPKCEState(Base):
    """Transient PKCE state for the external OAuth relay (plugin_oauth_pkce_state from core_008 + core_012)."""

    __tablename__ = "plugin_oauth_pkce_state"

    state = Column(String, primary_key=True)
    plugin_id = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    # pgp_sym_encrypt(verifier, master_key)
    code_verifier = Column(LargeBinary, nullable=False)
    redirect_uri = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Arbitrary JSON context (added by core_012)
    context = Column(JSON, nullable=True)


class MCPBearerToken(Base):
    """
    Opaque bearer tokens issued by the FastMCP adapter (mcp_bearer_tokens from core_011).

    The adapter stores a mapping of opaque_token → (jti, client_id) so
    FastMCP's db_load_token path can resolve the real JWT JTI for revocation.
    """

    __tablename__ = "mcp_bearer_tokens"

    token = Column(String, primary_key=True)
    client_id = Column(String, nullable=False)
    scopes = Column(ARRAY(TEXT), nullable=False)
    expires_at = Column(BigInteger, nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)


class MCPPendingAuth(Base):
    """Persisted pending OAuth state for Layer 1 restart recovery (mcp_pending_auths from core_013).

    ``data`` is a JSON blob containing the fields stashed by OAuthService_FastMCPProvider.authorize()
    (client_id, code_challenge, code_challenge_method, redirect_uri, scopes, state).
    Using plain TEXT rather than JSONB so the column never requires pgcrypto — the pending
    state contains no secrets (challenge is already hashed; verifier is never stored).
    """

    __tablename__ = "mcp_pending_auths"

    auth_state = Column(String, primary_key=True)
    data = Column(Text, nullable=False)          # JSON blob
    expires_at = Column(DateTime(timezone=True), nullable=False)


class Tenant(Base):
    """Tenant model representing logical tenant partitioning (MTU-2abc)."""

    __tablename__ = "tenants"
    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    name       = Column(Text, nullable=False, unique=True)
    slug       = Column(Text, nullable=False, unique=True)
    is_active  = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class ApiKey(Base):
    """API key model for long-lived access tokens (core_029)."""

    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    key_id = Column(Text, unique=True, nullable=False)
    subject = Column(Text, nullable=False, default="admin")
    name = Column(Text, nullable=False)
    prefix = Column(Text, nullable=False)
    token_hash = Column(Text, unique=True, nullable=False)
    value = Column(LargeBinary, nullable=False)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_used_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    scopes = Column(JSONB, nullable=True)
    tenant_id = Column(BigInteger, nullable=True, index=True)


class ApiKeyScopePreset(Base):
    """Saved custom scope selection for reuse when configuring API keys (core_031)."""

    __tablename__ = "api_key_scope_presets"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    subject = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    scopes = Column(JSONB, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    tenant_id = Column(BigInteger, nullable=True, index=True)


class User(Base):
    """User model representing login credentials, role, and tenant membership."""

    __tablename__ = "users"
    id            = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    username      = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)  # argon2 (new) or bcrypt (legacy-seeded)
    role          = Column(Text, nullable=False, server_default=text("'operator'"))
    tenant_id     = Column(BigInteger, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True)  # Layer 2 seam
    is_active     = Column(Boolean, nullable=False, server_default=text("true"))
    created_at    = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))

