"""SQLAlchemy declarative base, shared by every model submodule.
Split out of db_layer/models.py (Phase 5 modularity refactor)."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Base model for SQLAlchemy classes.
    """
    pass
