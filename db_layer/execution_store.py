"""
Database persistence store for workflow executions (core_025).
"""

import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from db_layer.connection import get_async_session
from db_layer.models import WorkflowExecution

logger = logging.getLogger("whiskers")


async def create_execution(
    session_id: str | None,
    workflow_plan_id: str | None,
    user_query: str,
    iteration: int = 0,
) -> str:
    """Create a workflow execution record in the database and return its string UUID."""
    wp_uuid = None
    if workflow_plan_id:
        try:
            wp_uuid = UUID(workflow_plan_id)
        except ValueError:
            logger.warning("create_execution: Invalid workflow_plan_id '%s'", workflow_plan_id)

    async with get_async_session() as session:
        exec_obj = WorkflowExecution(
            session_id=session_id,
            workflow_plan_id=wp_uuid,
            user_query=user_query,
            iteration=iteration,
            step_results=[],
            working_memory={},
            carry={},
            status="running",
        )
        session.add(exec_obj)
        await session.flush()
        exec_id = str(exec_obj.id)
        await session.commit()
        return exec_id


async def finalize_execution(
    exec_id: str,
    step_results: list,
    working_memory: dict,
    summary: str | None,
    content: str | None,
    carry: dict,
    status: str = "done",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    model_usage: dict | None = None,
) -> None:
    """Finalize a workflow execution record with results, summary and status."""
    try:
        uuid_val = UUID(exec_id)
    except ValueError:
        logger.warning("finalize_execution: Invalid exec_id '%s'", exec_id)
        return

    # The `content` column is VARCHAR; the summary node may emit a structured dict.
    # Serialize non-string content to JSON so the write never fails to adapt.
    if content is not None and not isinstance(content, str):
        import json
        try:
            content = json.dumps(content, default=str)
        except (TypeError, ValueError):
            content = str(content)

    async with get_async_session() as session:
        stmt = (
            update(WorkflowExecution)
            .where(WorkflowExecution.id == uuid_val)
            .values(
                step_results=step_results,
                working_memory=working_memory,
                summary=summary,
                content=content,
                carry=carry,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                model_usage=model_usage or {},
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def get_execution(exec_id: str) -> dict | None:
    """Retrieve a single workflow execution by string UUID."""
    try:
        uuid_val = UUID(exec_id)
    except ValueError:
        return None

    async with get_async_session() as session:
        stmt = select(WorkflowExecution).where(WorkflowExecution.id == uuid_val)
        result = await session.execute(stmt)
        exec_obj = result.scalar_one_or_none()
        if not exec_obj:
            return None
        return {
            "id": str(exec_obj.id),
            "session_id": exec_obj.session_id,
            "workflow_plan_id": str(exec_obj.workflow_plan_id) if exec_obj.workflow_plan_id else None,
            "user_query": exec_obj.user_query,
            "iteration": exec_obj.iteration,
            "step_results": exec_obj.step_results,
            "working_memory": exec_obj.working_memory,
            "summary": exec_obj.summary,
            "content": exec_obj.content,
            "carry": exec_obj.carry,
            "status": exec_obj.status,
            "created_at": exec_obj.created_at.isoformat() if exec_obj.created_at else None,
            "updated_at": exec_obj.updated_at.isoformat() if exec_obj.updated_at else None,
        }


async def list_executions(session_id: str, limit: int = 50) -> list[dict]:
    """Retrieve a list of executions for a specific session."""
    async with get_async_session() as session:
        stmt = (
            select(WorkflowExecution)
            .where(WorkflowExecution.session_id == session_id)
            .order_by(WorkflowExecution.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        executions = result.scalars().all()
        return [
            {
                "id": str(exec_obj.id),
                "session_id": exec_obj.session_id,
                "workflow_plan_id": str(exec_obj.workflow_plan_id) if exec_obj.workflow_plan_id else None,
                "user_query": exec_obj.user_query,
                "iteration": exec_obj.iteration,
                "step_results": exec_obj.step_results,
                "working_memory": exec_obj.working_memory,
                "summary": exec_obj.summary,
                "content": exec_obj.content,
                "carry": exec_obj.carry,
                "status": exec_obj.status,
                "created_at": exec_obj.created_at.isoformat() if exec_obj.created_at else None,
                "updated_at": exec_obj.updated_at.isoformat() if exec_obj.updated_at else None,
            }
            for exec_obj in executions
        ]
