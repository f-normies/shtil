"""Контракты закрытого S3-адаптера."""

from io import BytesIO
from pathlib import Path
from typing import Any

import boto3.session
import botocore.session
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from clin_compass.core import storage
from clin_compass.core.config import Settings


def test_unconfigured_storage_does_not_create_an_aws_client() -> None:
    # S3 задаём явно: Makefile экспортирует .env разработчика, там адрес его RustFS.
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        s3_endpoint_url=None,
        s3_access_key=None,
        s3_secret_key=None,
        s3_bucket=None,
        s3_region=None,
    )

    assert storage.build_storage(settings) is None


def test_shared_test_settings_never_reach_developer_storage(settings: Settings) -> None:
    assert storage.build_storage(settings) is None


def _private_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        s3_endpoint_url="http://rustfs.test:9000",
        s3_access_key="private-access",
        s3_secret_key="private-secret",
        s3_bucket="private-audio",
        s3_region="us-east-1",
    )


def test_storage_ignores_unknown_aws_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    # Чужой AWS_PROFILE не должен ронять старт API и worker с ProfileNotFound.
    monkeypatch.setenv("AWS_PROFILE", "does-not-exist")

    store = storage.build_storage(_private_settings())

    assert store is not None
    assert store._client.meta.region_name == "us-east-1"
    assert store._client.meta.endpoint_url == "http://rustfs.test:9000"


def test_storage_ignores_shared_aws_config_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config"
    config.write_text("[default]\ndefaults_mode = bogus\n", encoding="utf-8")
    credentials = tmp_path / "credentials"
    credentials.write_text("not an ini file\n", encoding="utf-8")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))

    store = storage.build_storage(_private_settings())

    assert store is not None
    assert store._client.meta.region_name == "us-east-1"
    assert store._client.meta.endpoint_url == "http://rustfs.test:9000"


def test_s3_adapter_uses_explicit_private_endpoint_credentials_and_bounded_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: dict[str, Any] = {}

    class Client:
        def put_object(self, **kwargs: Any) -> None:
            made["put"] = kwargs

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            made["get"] = kwargs
            return {
                "Body": BytesIO(b"audio"),
                "ContentLength": 5,
                "ContentType": "audio/webm",
                "ContentRange": "bytes 1-3/5",
            }

        def delete_object(self, **kwargs: Any) -> None:
            made["delete"] = kwargs

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            made["session"] = kwargs

        def client(self, service_name: str, **kwargs: Any) -> Client:
            made["client_name"] = service_name
            made["client_options"] = kwargs
            return Client()

    monkeypatch.setattr(boto3.session, "Session", Session)
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        s3_endpoint_url="http://rustfs.test:9000",
        s3_access_key="private-access",
        s3_secret_key="private-secret",
        s3_bucket="private-audio",
        s3_region="us-east-1",
    )

    store = storage.build_storage(settings)
    assert store is not None
    store.put("attempts/opaque-key", b"audio", "audio/webm")
    item = store.get("attempts/opaque-key", byte_range="bytes=1-3")
    item.body.close()
    store.delete("attempts/opaque-key")

    assert isinstance(made["session"].pop("botocore_session"), botocore.session.Session)
    assert made["session"] == {
        "aws_access_key_id": "private-access",
        "aws_secret_access_key": "private-secret",
        "region_name": "us-east-1",
    }
    assert made["client_name"] == "s3"
    client_options = made["client_options"]
    assert client_options["endpoint_url"] == "http://rustfs.test:9000"
    assert client_options["region_name"] == "us-east-1"
    config = client_options["config"]
    assert isinstance(config, Config)
    assert config.connect_timeout > 0
    assert config.read_timeout > 0
    assert config.retries["total_max_attempts"] <= 3
    assert config.s3["addressing_style"] == "path"
    assert made["put"] == {
        "Bucket": "private-audio",
        "Key": "attempts/opaque-key",
        "Body": b"audio",
        "ContentType": "audio/webm",
    }
    assert made["get"] == {
        "Bucket": "private-audio",
        "Key": "attempts/opaque-key",
        "Range": "bytes=1-3",
    }
    assert made["delete"] == {"Bucket": "private-audio", "Key": "attempts/opaque-key"}


def test_s3_missing_object_has_a_safe_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    class Session:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def client(self, service_name: str, **kwargs: Any) -> Client:
            return Client()

    monkeypatch.setattr(boto3.session, "Session", Session)
    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        s3_endpoint_url="http://rustfs.test:9000",
        s3_access_key="private-access",
        s3_secret_key="private-secret",
        s3_bucket="private-audio",
        s3_region="us-east-1",
    )
    store = storage.build_storage(settings)
    assert store is not None

    with pytest.raises(storage.StorageObjectMissingError) as caught:
        store.get("attempts/missing")

    assert "NoSuchKey" not in str(caught.value)
