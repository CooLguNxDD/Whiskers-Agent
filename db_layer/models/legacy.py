# ---------------------------------------------------------------------------
# Legacy models — predating the PG-native encrypted schema (core_001…core_010).
# Kept as compatibility shims until db_layer/oauth_store.py callers are migrated
# to OAuthService in a follow-up PR.  DO NOT remove the OAuthClient alias until
# that follow-up PR merges.
# ---------------------------------------------------------------------------
from sqlalchemy import Column, Float, JSON, String
from db_layer.models.base import Base


class OAuthClient(Base):
    """
    LEGACY — DO NOT USE IN NEW CODE.

    Predates the pgcrypto-encrypted oauth_clients schema from core_001…core_010.
    The column layout diverges from what the migrations actually built.  Kept as
    a compatibility shim (aliased to OAuthClientLegacy below) so existing
    db_layer/oauth_store.py imports do not break during this PR cycle.

    Removal target: the follow-up PR that rewrites oauth_store.py callers to
    use OAuthService / OAuthClientV2.
    """
    __tablename__ = "oauth_clients"

    client_id = Column(String, primary_key=True)
    # Full OAuthClientInformationFull serialised as JSON
    client_data = Column(JSON, nullable=False)


class OAuthAccessToken(Base):
    """
    LEGACY — DO NOT USE IN NEW CODE.

    Predates the JTI-based oauth_tokens schema from core_001…core_010.
    Kept as a compatibility shim for db_layer/oauth_store.py.

    Removal target: same follow-up PR as OAuthClient above.
    """
    __tablename__ = "oauth_access_tokens"

    token = Column(String, primary_key=True)
    client_id = Column(String, nullable=False, index=True)
    scopes = Column(JSON, nullable=True)
    expires_at = Column(Float, nullable=True)


# Alias so any code that imports OAuthClient still works while reviewers can
# see the deprecation clearly.
OAuthClientLegacy = OAuthClient
