from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    Float,
    Integer,
    String,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from db_layer.models.base import Base


class PortfolioProject(Base):
    """Portfolio project record.

    Metrics items are {"label", "value", optional "headline": true} — headline
    metrics are promoted into the statStrip block by the portfolio composer.

    context_sources items follow the sources.yaml shape:
    {"kind": "github"|"url"|"notion"|"gdoc", "ref": "...", "use"?: [...],
     "id"?: "optional-source-id"}.

    started_on/ended_on: project timeline, parsed from source prose by
    discovery (discovery/period.py) or set by an operator. ended_on IS
    NULL means ongoing. timeline_source in {"notion_period","github",
    "manual"} — manual values are never overwritten by discovery.
    """

    __tablename__ = "portfolio_projects"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    slug = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    summary = Column(Text, nullable=False)
    tags = Column(JSONB, server_default="[]", nullable=False)
    metrics = Column(JSONB, server_default="[]", nullable=False)
    links = Column(JSONB, server_default="[]", nullable=False)
    audiences = Column(JSONB, server_default="[]", nullable=False)
    sort_order = Column(Integer, server_default="0", nullable=False)
    is_active = Column(Boolean, server_default="true", nullable=False)
    context_sources = Column(JSONB, server_default="[]", nullable=False)
    # Project timeline — ended_on IS NULL means ongoing, not unknown;
    # started_on IS NULL means unknown. timeline_source drives reconcile
    # precedence (notion_period beats github) and the manual-edit lock.
    started_on = Column(Date, nullable=True)
    ended_on = Column(Date, nullable=True)
    timeline_source = Column(String, nullable=True)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PortfolioJobLayout(Base):
    """Job-specific pre-baked portfolio layout artifact ("bake & send").

    Persisted once per job application bake, keyed by a short URL-safe id
    (see utils/short_id.py) baked into the tailored resume's contact header
    as ``?j=<short_id>``. Served read-only, publicly, with no LLM call.
    """

    __tablename__ = "portfolio_job_layouts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_id = Column(String, nullable=False, unique=True, index=True)
    job_application_job_id = Column(String, nullable=True, index=True)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    audience = Column(String, nullable=False)
    star_query = Column(Text, nullable=True)
    layout_json = Column(JSONB, nullable=False)
    # Durable provenance (Phase 3) -- vectors in portfolio_plugin__layouts are
    # the fuzzy index; these are the queryable ground truth for one bake.
    plan_json = Column(JSONB, nullable=True)
    recipe_id = Column(String, nullable=True)
    jury_score = Column(Float, nullable=True)
    # Chat patch forks: HR-facing bake rows stay immutable; only is_derived=true
    # rows may be updated. parent_short_id points at the original bake.
    parent_short_id = Column(String, nullable=True, index=True)
    is_derived = Column(Boolean, nullable=False, server_default="false")
    # Which rung of the compose ladder shipped this row, promoted out of
    # layout_json.meta so "what fraction of bakes degraded" is one SELECT.
    compose_path = Column(String, nullable=True, index=True)
    mode = Column(String, nullable=True)
    degraded = Column(Boolean, nullable=False, server_default="false", index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    # Derived rows are mutated in place by chat patches; without this there is
    # no invalidation signal for the 5-minute public cache.
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PortfolioBakeRun(Base):
    """One bake attempt — including the ones that never produced a layout.

    ``portfolio_job_layouts`` only records bakes that shipped, and telemetry
    aggregates are minute-bucketed and in-memory. This is the durable per-run
    debug artifact: which rung won, which rungs declined and why, and how long
    each stage took. ``short_id`` is NULL for a failed bake — that is the point.
    """

    __tablename__ = "portfolio_bake_runs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, unique=True, index=True)
    tenant_id = Column(BigInteger, nullable=False, index=True)
    short_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False)
    compose_path = Column(String, nullable=True)
    mode = Column(String, nullable=True)
    degraded = Column(Boolean, nullable=False, server_default="false")
    company = Column(String, nullable=True)
    role = Column(String, nullable=True)
    job_brief_hash = Column(String, nullable=True, index=True)
    evidence_pack_hash = Column(String, nullable=True)
    stages_json = Column(JSONB, nullable=True)
    errors_json = Column(JSONB, nullable=True)
    total_ms = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


# Public visitor path — widths match GraphRunEvent style so a CatPortfolio
# client cannot write unbounded TEXT into the audit row.
ASK_TURN_RUN_ID_MAX = 64
ASK_TURN_SUBJECT_MAX = 256
ASK_TURN_SESSION_MAX = 64
ASK_TURN_INTENT_MAX = 64
ASK_TURN_VIEW_MAX = 64
ASK_TURN_SLUG_MAX = 256
ASK_TURN_PARENT_RUN_ID_MAX = 64
ASK_TURN_QUESTION_MAX = 500


class PortfolioAskTurn(Base):
    """One fish-tank visitor ask turn — who asked what, and what happened.

    Mirrors PortfolioBakeRun's dual-write shape (see ask/telemetry.py). Ask
    stays ephemeral in every other sense — this row never stores the overlay
    blocks or the generated answer, only the question (truncated) and outcome,
    so a re-ask still recomputes rather than replaying a cached patch.
    """

    __tablename__ = "portfolio_ask_turns"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(ASK_TURN_RUN_ID_MAX), nullable=False, unique=True, index=True)
    tenant_id = Column(BigInteger, nullable=False, index=True)
    subject = Column(String(ASK_TURN_SUBJECT_MAX), nullable=True)
    visitor_session_id = Column(String(ASK_TURN_SESSION_MAX), nullable=True, index=True)
    question = Column(String, nullable=False)
    intent = Column(String(ASK_TURN_INTENT_MAX), nullable=True, index=True)
    view = Column(String(ASK_TURN_VIEW_MAX), nullable=True)
    focus_slug = Column(String(ASK_TURN_SLUG_MAX), nullable=True)
    highlight_slugs = Column(JSONB, nullable=True)
    add_slugs = Column(JSONB, nullable=True)
    ok = Column(Boolean, nullable=False, server_default="true")
    error_type = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    parent_run_id = Column(String(ASK_TURN_PARENT_RUN_ID_MAX), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
