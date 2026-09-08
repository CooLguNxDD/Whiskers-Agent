"""Unit tests for goal_store.py"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4
from datetime import datetime, timezone

from db_layer.goal_store import create_goal, get_goal, update_goal_status, update_goal_result
from db_layer.models import Goal

@pytest.fixture
def mock_session():
    with patch("db_layer.goal_store.get_async_session") as mock_get_session:
        mock_session_instance = AsyncMock()
        mock_session_instance.add = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session_instance
        yield mock_session_instance

@pytest.mark.asyncio
async def test_create_goal(mock_session):
    def fake_add(obj):
        obj.id = uuid4()
    mock_session.add.side_effect = fake_add
    
    goal_id = await create_goal("Test raw goal", {"test": "spec"})
    
    assert goal_id is not None
    assert isinstance(goal_id, str)
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.commit.assert_called_once()
    
    args, _ = mock_session.add.call_args
    goal = args[0]
    assert goal.raw_goal == "Test raw goal"
    assert goal.goal_spec == {"test": "spec"}
    assert goal.status == "queued"

@pytest.mark.asyncio
async def test_get_goal_found(mock_session):
    goal_id = uuid4()
    mock_goal = MagicMock()
    mock_goal.id = goal_id
    mock_goal.raw_goal = "Some goal"
    mock_goal.goal_spec = {}
    mock_goal.status = "queued"
    mock_goal.result = None
    mock_goal.created_at = datetime.now(timezone.utc)
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_goal
    mock_session.execute.return_value = mock_result
    
    res = await get_goal(str(goal_id))
    
    assert res is not None
    assert res["id"] == str(goal_id)
    assert res["raw_goal"] == "Some goal"
    assert res["status"] == "queued"
    mock_session.execute.assert_called_once()

@pytest.mark.asyncio
async def test_get_goal_not_found(mock_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    res = await get_goal(str(uuid4()))
    assert res is None

@pytest.mark.asyncio
async def test_get_goal_invalid_uuid(mock_session):
    res = await get_goal("invalid-uuid")
    assert res is None
    mock_session.execute.assert_not_called()

@pytest.mark.asyncio
async def test_update_goal_status(mock_session):
    goal_id = str(uuid4())
    await update_goal_status(goal_id, "running")
    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_update_goal_result(mock_session):
    goal_id = str(uuid4())
    await update_goal_result(goal_id, {"some": "result"})
    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()
