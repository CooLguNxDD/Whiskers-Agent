import pytest
from sqlalchemy import text
from core.context import vault
from db_layer.connection import get_async_session
from db_layer.plugin_registry_store import DBPluginRegistry
from terminal.script.add_credentials import add_credential


@pytest.mark.asyncio
async def test_add_credential_script():
    if vault is None:
        pytest.skip("Vault/database not available in this environment")

    plugin_id = "test_credentials_plugin"
    key_name = "TEST_API_KEY"
    value = "test_value_123"

    db_registry = DBPluginRegistry()

    # Pre-register the plugin to satisfy foreign key constraint
    await db_registry.register({
        "name": plugin_id,
        "version": "1.0.0",
        "required_credentials": [key_name]
    })

    try:
        # Run script logic
        await add_credential(plugin_id, key_name, value)

        # Retrieve value from Vault and verify it matches
        val = await vault.get(plugin_id, key_name)
        assert val == value
    finally:
        # Cleanup credential and plugin
        await vault.delete(plugin_id, key_name)
        async with get_async_session() as session:
            await session.execute(
                text("DELETE FROM plugins WHERE id = :pid"),
                {"pid": plugin_id}
            )
            await session.commit()
