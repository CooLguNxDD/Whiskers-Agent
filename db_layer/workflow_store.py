"""
Database persistence store for compiled YAML workflow plans (core_020).
"""

import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from db_layer.connection import get_async_session
from db_layer.models import WorkflowPlan

logger = logging.getLogger("whiskers")


async def save_workflow_plan(
    name: str,
    user_query: str,
    yaml_content: str,
    compiled_plan: list,
    outputs: dict,
    llm_provider: str,
    llm_model: str,
    embed_model: str | None = None,
    status: str = "generated",
    session_id: str | None = None,
) -> str:
    """Save a workflow plan to the database and return its string UUID."""
    async with get_async_session() as session:
        plan = WorkflowPlan(
            name=name,
            user_query=user_query,
            yaml_content=yaml_content,
            compiled_plan=compiled_plan,
            outputs=outputs,
            llm_provider=llm_provider,
            llm_model=llm_model,
            embed_model=embed_model,
            status=status,
            session_id=session_id,
        )
        session.add(plan)
        await session.flush()
        plan_id = str(plan.id)
        await session.commit()
        return plan_id


async def mark_executed(plan_id: str) -> None:
    """Update workflow plan status to 'executed' and set last_executed_at."""
    try:
        uuid_val = UUID(plan_id)
    except ValueError:
        logger.warning("mark_executed: Invalid plan_id '%s'", plan_id)
        return

    async with get_async_session() as session:
        stmt = (
            update(WorkflowPlan)
            .where(WorkflowPlan.id == uuid_val)
            .values(status="executed", last_executed_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()


async def get_workflow_plan(plan_id: str) -> dict | None:
    """Retrieve a single workflow plan by string UUID."""
    try:
        uuid_val = UUID(plan_id)
    except ValueError:
        return None

    async with get_async_session() as session:
        stmt = select(WorkflowPlan).where(WorkflowPlan.id == uuid_val)
        result = await session.execute(stmt)
        plan = result.scalar_one_or_none()
        if not plan:
            return None
        return {
            "id": str(plan.id),
            "session_id": plan.session_id,
            "name": plan.name,
            "user_query": plan.user_query,
            "yaml_content": plan.yaml_content,
            "compiled_plan": plan.compiled_plan,
            "outputs": plan.outputs,
            "llm_provider": plan.llm_provider,
            "llm_model": plan.llm_model,
            "embed_model": plan.embed_model,
            "status": plan.status,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "last_executed_at": plan.last_executed_at.isoformat() if plan.last_executed_at else None,
        }


async def list_workflow_plans(limit: int = 20) -> list[dict]:
    """Retrieve a list of recent workflow plans."""
    async with get_async_session() as session:
        stmt = select(WorkflowPlan).order_by(WorkflowPlan.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        plans = result.scalars().all()
        return [
            {
                "id": str(plan.id),
                "session_id": plan.session_id,
                "name": plan.name,
                "user_query": plan.user_query,
                "yaml_content": plan.yaml_content,
                "compiled_plan": plan.compiled_plan,
                "outputs": plan.outputs,
                "llm_provider": plan.llm_provider,
                "llm_model": plan.llm_model,
                "embed_model": plan.embed_model,
                "status": plan.status,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "last_executed_at": plan.last_executed_at.isoformat() if plan.last_executed_at else None,
            }
            for plan in plans
        ]
