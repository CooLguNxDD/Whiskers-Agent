import pytest
from sqlalchemy import LargeBinary

def test_api_key_model_definition():
    # from db_layer.models import ApiKey succeeds and ApiKey.__tablename__ == "api_keys"
    from db_layer.models import ApiKey
    assert ApiKey.__tablename__ == "api_keys"

    # Assert mapped columns are exactly the expected list
    expected_columns = [
        "id",
        "key_id",
        "subject",
        "name",
        "prefix",
        "token_hash",
        "value",
        "status",
        "expires_at",
        "last_used_at",
        "created_at",
        "revoked_at",
        "scopes",
        "tenant_id",
    ]
    actual_columns = ApiKey.__table__.columns.keys()
    assert len(actual_columns) == len(expected_columns)
    for col in expected_columns:
        assert col in actual_columns

    # ApiKey.value is a LargeBinary column type
    assert isinstance(ApiKey.value.type, LargeBinary)


def test_api_key_migration():
    # Assert core_029 migration attributes
    import migrations.versions.core.core_029_api_keys as migration
    assert migration.revision == "core_029"
    assert migration.down_revision == "core_028"
    assert migration.branch_labels is None
