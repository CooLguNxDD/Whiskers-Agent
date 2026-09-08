"""
Database persistence store for goals.
"""

import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from db_layer.connection import get_async_session
from db_layer.models import Goal

logger = logging.getLogger("whiskers")


async def create_goal(raw_goal: str, goal_spec: dict | None = None) -> str:
    """Creates a Goal row. Returns str(goal.id)."""
    async with get_async_session() as session:
        goal = Goal(
            raw_goal=raw_goal,
            goal_spec=goal_spec,
            status="queued"
        )
        session.add(goal)
        await session.flush()
        goal_id = str(goal.id)
        await session.commit()
        return goal_id

async def get_goal(goal_id: str) -> dict | None:
    """Returns dict with id, raw_goal, goal_spec, status, result, created_at. Returns None if not found."""
    try:
        uuid_val = UUID(goal_id)
    except ValueError:
        return None

    async with get_async_session() as session:
        stmt = select(Goal).where(Goal.id == uuid_val)
        result = await session.execute(stmt)
        goal = result.scalar_one_or_none()
        if not goal:
            return None
        return {
            "id": str(goal.id),
            "raw_goal": goal.raw_goal,
            "goal_spec": goal.goal_spec,
            "status": goal.status,
            "result": goal.result,
            "created_at": goal.created_at.isoformat() if goal.created_at else None,
        }

async def update_goal_status(goal_id: str, status: str) -> None:
    """Updates Goal.status. Valid statuses: queued, running, done, failed."""
    try:
        uuid_val = UUID(goal_id)
    except ValueError:
        logger.warning("update_goal_status: Invalid goal_id '%s'", goal_id)
        return

    async with get_async_session() as session:
        stmt = (
            update(Goal)
            .where(Goal.id == uuid_val)
            .values(status=status)
        )
        await session.execute(stmt)
        await session.commit()

async def update_goal_result(goal_id: str, result: dict) -> None:
    """Updates Goal.result and sets status='done'."""
    try:
        uuid_val = UUID(goal_id)
    except ValueError:
        logger.warning("update_goal_result: Invalid goal_id '%s'", goal_id)
        return

    async with get_async_session() as session:
        stmt = (
            update(Goal)
            .where(Goal.id == uuid_val)
            .values(result=result, status="done")
        )
        await session.execute(stmt)
        await session.commit()
