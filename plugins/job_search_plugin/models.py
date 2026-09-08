from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    text,
)
from sqlalchemy import Computed
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector
from db_layer.models.base import Base


class JobApplicantProfile(Base):
    """Job applicant profile record."""

    __tablename__ = "job_applicant_profiles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    base_resume_text = Column(Text, nullable=True)
    cover_letter_template = Column(Text, nullable=True)
    resume_object_key = Column(String, nullable=True)
    preferences_text = Column(Text, nullable=True)
    # Compact/skills_only tier targeting fields (Stage 1, 0005 migration).
    location = Column(String, nullable=True)
    target_titles = Column(JSONB, server_default="[]")
    top_skills = Column(JSONB, server_default="[]")
    constraints_text = Column(Text, nullable=True)  # not "constraints" — shadows Table.constraints
    notice_period_days = Column(Integer, nullable=True)
    core_technologies = Column(JSONB, server_default="[]")
    methodologies = Column(JSONB, server_default="[]")
    languages = Column(JSONB, server_default="[]")
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class JobApplication(Base):
    """Job application tracking record."""

    __tablename__ = "job_applications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    applicant_profile_id = Column(
        BigInteger,
        ForeignKey("job_applicant_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    provider_application_id = Column(String, nullable=True)
    status = Column(String, nullable=False, server_default="drafted")
    notes = Column(Text, nullable=True)
    resume_object_key = Column(String, nullable=True)
    cover_letter_object_key = Column(String, nullable=True)
    portfolio_job_id = Column(String, nullable=True, index=True)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))


class JobPostingLiveness(Base):
    """Tracks posting lifecycle, freshness, repost count, and ghost-job signals."""

    __tablename__ = "job_posting_liveness"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_hash = Column(String(64), unique=True, index=True, nullable=False)
    url = Column(Text, nullable=False)
    company = Column(String, nullable=False, index=True)
    role_title = Column(String, nullable=False)
    provider = Column(String, nullable=True)  # "ashby", "greenhouse", "lever", "generic_web"
    first_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    last_verified_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    is_active = Column(Boolean, default=True, nullable=False)
    repost_count = Column(Integer, default=1, nullable=False)
    staleness_days = Column(Integer, default=0, nullable=False)
    ghost_signals = Column(JSON, server_default="{}")
    tenant_id = Column(BigInteger, nullable=True, index=True)


class JobPreferenceEmbedding(Base):
    """Vector embeddings for job applicant preferences and resume chunks."""

    __tablename__ = "job_preference_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "applicant_profile_id",
            "source",
            "content_hash",
            name="job_pref_embeddings_profile_source_hash_key",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    applicant_profile_id = Column(
        BigInteger,
        ForeignKey("job_applicant_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source = Column(String, nullable=False)  # "resume" | "preferences"
    content = Column(Text, nullable=False)
    embedding = Column(Vector(), nullable=True)
    model = Column(String, nullable=False)
    meta = Column(JSON, server_default="{}")
    content_hash = Column(String, nullable=False)
    tenant_id = Column(BigInteger, nullable=True, index=True)
    synced_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("now()"))
    search_doc = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content, ''))", persisted=True),
    )
