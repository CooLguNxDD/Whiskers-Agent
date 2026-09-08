import pytest
from unittest.mock import patch, AsyncMock
import db_layer.workflow_store as store


@pytest.mark.asyncio
async def test_workflow_store_crud_mocked():
    mock_id = "12345678-1234-5678-1234-567812345678"
    mock_plan = {
        "id": mock_id,
        "name": "test-workflow",
        "user_query": "create a record",
        "yaml_content": "steps:\n  - operation_id: create_record",
        "compiled_plan": [],
        "outputs": {},
        "llm_provider": "openai",
        "llm_model": "gpt-4o",
        "embed_model": "text-embedding-3-small",
        "status": "generated",
        "created_at": None,
        "last_executed_at": None,
    }

    with patch("db_layer.workflow_store.save_workflow_plan", new_callable=AsyncMock, return_value=mock_id), \
         patch("db_layer.workflow_store.get_workflow_plan", new_callable=AsyncMock, return_value=mock_plan), \
         patch("db_layer.workflow_store.mark_executed", new_callable=AsyncMock) as mock_mark:
        
        plan_id = await store.save_workflow_plan(
            name="test-workflow",
            user_query="create a record",
            yaml_content="steps:\n  - operation_id: create_record",
            compiled_plan=[],
            outputs={},
            llm_provider="openai",
            llm_model="gpt-4o",
            embed_model="text-embedding-3-small"
        )
        assert plan_id == mock_id
        
        retrieved = await store.get_workflow_plan(plan_id)
        assert retrieved == mock_plan
        
        await store.mark_executed(plan_id)
        mock_mark.assert_called_once_with(mock_id)
