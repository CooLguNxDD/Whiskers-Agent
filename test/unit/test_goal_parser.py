import json
import pytest
import unittest.mock as mock

from core.goal_parser import parse_goal, SYSTEM_PROMPT
from core.goal_spec import TaskType


@pytest.mark.asyncio
async def test_parse_goal_empty_raises_value_error():
    with pytest.raises(ValueError, match="raw goal must be non-empty"):
        await parse_goal("")
        
    with pytest.raises(ValueError, match="raw goal must be non-empty"):
        await parse_goal("   \n ")


@pytest.mark.asyncio
@mock.patch("core.goal_parser._LLM_AVAILABLE", False)
async def test_parse_goal_llm_unavailable_fallback():
    spec = await parse_goal("Do something simple", {"key": "value"})
    
    assert spec.raw_goal == "Do something simple"
    assert spec.intent == "Do something simple"
    assert spec.success_criteria == "Complete the goal as described"
    assert spec.context == {"key": "value"}
    assert len(spec.tasks) == 1
    
    task = spec.tasks[0]
    assert task.id == "task_0"
    assert task.task_type == TaskType.REASONING
    assert task.intent == "Do something simple"
    assert task.depends_on == []
    assert task.model is not None  # Assigned by assign_models


@pytest.mark.asyncio
@mock.patch("core.goal_parser._LLM_AVAILABLE", True)
@mock.patch("core.goal_parser.get_chat_llm")
async def test_parse_goal_happy_path(mock_get_chat_llm):
    mock_llm = mock.AsyncMock()
    mock_response = mock.Mock()
    
    # Mock valid JSON response
    mock_response.content = json.dumps({
        "intent": "Retrieve and format data",
        "tasks": [
            {
                "id": "task_0",
                "task_type": "data_retrieval",
                "intent": "Get the data",
                "depends_on": []
            }
        ],
        "success_criteria": "Data is retrieved and formatted"
    })
    mock_llm.ainvoke.return_value = mock_response
    mock_get_chat_llm.return_value = mock_llm
    
    spec = await parse_goal("Get data")
    
    assert spec.intent == "Retrieve and format data"
    assert spec.success_criteria == "Data is retrieved and formatted"
    assert len(spec.tasks) == 1
    
    task = spec.tasks[0]
    assert task.id == "task_0"
    assert task.task_type == TaskType.DATA_RETRIEVAL
    assert task.intent == "Get the data"
    assert task.depends_on == []


@pytest.mark.asyncio
@mock.patch("core.goal_parser._LLM_AVAILABLE", True)
@mock.patch("core.goal_parser.get_chat_llm")
async def test_parse_goal_malformed_json_fallback(mock_get_chat_llm):
    mock_llm = mock.AsyncMock()
    mock_response = mock.Mock()
    
    # Mock invalid JSON response
    mock_response.content = "This is not json { foo }"
    mock_llm.ainvoke.return_value = mock_response
    mock_get_chat_llm.return_value = mock_llm
    
    spec = await parse_goal("Try this")
    
    # Should fall back
    assert spec.raw_goal == "Try this"
    assert len(spec.tasks) == 1
    assert spec.tasks[0].id == "task_0"
    assert spec.tasks[0].task_type == TaskType.REASONING


@pytest.mark.asyncio
@mock.patch("core.goal_parser._LLM_AVAILABLE", True)
@mock.patch("core.goal_parser.get_chat_llm")
async def test_parse_goal_multi_task(mock_get_chat_llm):
    mock_llm = mock.AsyncMock()
    mock_response = mock.Mock()
    
    mock_response.content = json.dumps({
        "intent": "Complex operation",
        "tasks": [
            {
                "id": "task_0",
                "task_type": "data_retrieval",
                "intent": "Get data",
                "depends_on": []
            },
            {
                "id": "task_1",
                "task_type": "reasoning",
                "intent": "Analyze data",
                "depends_on": ["task_0"]
            }
        ],
        "success_criteria": "Operation complete"
    })
    mock_llm.ainvoke.return_value = mock_response
    mock_get_chat_llm.return_value = mock_llm
    
    spec = await parse_goal("Do multi-step task")
    
    assert len(spec.tasks) == 2
    assert spec.tasks[0].id == "task_0"
    assert spec.tasks[1].id == "task_1"
    assert spec.tasks[1].depends_on == ["task_0"]
