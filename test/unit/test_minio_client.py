"""Unit tests for the MinIO client wrapper."""

import io
import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from core.artifact_store.minio_client import (
    DEFAULT_BUCKET,
    ensure_bucket,
    get_minio_client,
    get_presigned_url,
    put_object_bytes,
    reset_client_cache,
)


@pytest.fixture(autouse=True)
def clean_minio_cache():
    """Reset the MinIO client cache before and after each test."""
    reset_client_cache()
    yield
    reset_client_cache()


def test_get_minio_client_singleton(monkeypatch):
    """Verify get_minio_client returns the same instance on repeated calls (singleton) and uses env vars."""
    monkeypatch.setenv("MINIO_ENDPOINT", "minio-test:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("MINIO_SECURE", "true")

    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        mock_instance = MagicMock()
        mock_minio_cls.return_value = mock_instance

        # First call constructs the client
        client1 = get_minio_client()
        mock_minio_cls.assert_called_once_with(
            "minio-test:9000",
            access_key="test-access",
            secret_key="test-secret",
            secure=True,
        )

        # Second call returns the cached instance
        client2 = get_minio_client()
        assert client1 is client2
        mock_minio_cls.assert_called_once()


def test_get_minio_client_defaults(monkeypatch):
    """Verify get_minio_client uses defaults if secure env var is missing or false."""
    monkeypatch.setenv("MINIO_ENDPOINT", "minio-test:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret")
    monkeypatch.delenv("MINIO_SECURE", raising=False)

    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        client = get_minio_client()
        mock_minio_cls.assert_called_once_with(
            "minio-test:9000",
            access_key="test-access",
            secret_key="test-secret",
            secure=False,
        )


def test_ensure_bucket_creates_if_not_exists():
    """Verify ensure_bucket calls make_bucket only when bucket_exists returns False."""
    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = False

        ensure_bucket("test-bucket")

        mock_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_client.make_bucket.assert_called_once_with("test-bucket")


def test_ensure_bucket_does_not_create_if_exists():
    """Verify ensure_bucket does NOT call make_bucket when bucket_exists returns True."""
    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        ensure_bucket("test-bucket")

        mock_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_client.make_bucket.assert_not_called()


def test_put_object_bytes():
    """Verify put_object_bytes calls ensure_bucket + put_object with correct arguments."""
    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        
        # When put_object_bytes is called, ensure_bucket calls get_minio_client.
        # Mock ensure_bucket to verify it is called correctly.
        with patch("core.artifact_store.minio_client.ensure_bucket") as mock_ensure:
            test_data = b"hello pdf data"
            res_key = put_object_bytes("test-bucket", "resume.pdf", test_data, content_type="application/pdf")
            
            mock_ensure.assert_called_once_with("test-bucket")
            assert res_key == "resume.pdf"
            
            mock_client.put_object.assert_called_once()
            args, kwargs = mock_client.put_object.call_args
            assert args[0] == "test-bucket"
            assert args[1] == "resume.pdf"
            assert isinstance(args[2], io.BytesIO)
            assert args[2].getvalue() == test_data
            
            assert kwargs.get("length") == len(test_data)
            assert kwargs.get("content_type") == "application/pdf"


def test_get_presigned_url():
    """Verify get_presigned_url delegates to presigned_get_object with correct timedelta and returns URL."""
    with patch("core.artifact_store.minio_client.Minio") as mock_minio_cls:
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.presigned_get_object.return_value = "http://minio:9000/test-bucket/resume.pdf?signature=123"

        url = get_presigned_url("test-bucket", "resume.pdf", expires_seconds=1800)

        assert url == "http://minio:9000/test-bucket/resume.pdf?signature=123"
        mock_client.presigned_get_object.assert_called_once_with(
            "test-bucket",
            "resume.pdf",
            expires=timedelta(seconds=1800)
        )
