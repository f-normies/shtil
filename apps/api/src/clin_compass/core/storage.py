"""Минимальный адаптер закрытого S3-хранилища для голосовых сообщений."""

from dataclasses import dataclass
from typing import Any, BinaryIO, cast

import boto3.session
import botocore.session
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr

from clin_compass.core.config import Settings

_SDK_CONFIG = Config(
    connect_timeout=2,
    read_timeout=10,
    retries={"mode": "standard", "total_max_attempts": 3},
    s3={"addressing_style": "path"},
)
# Сессия botocore без профиля и файлов ~/.aws: AWS_PROFILE, AWS_CONFIG_FILE и
# AWS_SHARED_CREDENTIALS_FILE разработчика не должны ронять старт API и worker.
_NOT_FROM_ENVIRONMENT = (None, None, None, None)
_SESSION_VARS = {
    "profile": _NOT_FROM_ENVIRONMENT,
    "config_file": _NOT_FROM_ENVIRONMENT,
    "credentials_file": _NOT_FROM_ENVIRONMENT,
}


class StorageError(Exception):
    """Закрытая ошибка SDK, безопасная для обработки на границе приложения."""


class StorageUnavailableError(StorageError):
    """S3 не принял операцию или соединение оборвалось."""


class StorageObjectMissingError(StorageError):
    """Закрытый объект уже отсутствует в бакете."""


@dataclass
class StoredObject:
    """S3-ответ с потоком, который обязан закрыть вызывающий код."""

    body: BinaryIO
    size: int
    content_type: str
    content_range: str | None


class S3Storage:
    """Синхронная обёртка boto3; вызовы следует запускать вне async event loop."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Создать объект с аудио и доверенным базовым MIME."""
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError):
            raise StorageUnavailableError("S3 write failed") from None

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        """Открыть объект или его единственный byte range."""
        options: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        if byte_range is not None:
            options["Range"] = byte_range
        try:
            result = cast(dict[str, Any], self._client.get_object(**options))
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise StorageObjectMissingError("S3 object is missing") from None
            raise StorageUnavailableError("S3 read failed") from None
        except BotoCoreError:
            raise StorageUnavailableError("S3 read failed") from None
        return StoredObject(
            body=cast(BinaryIO, result["Body"]),
            size=int(result["ContentLength"]),
            content_type=result.get("ContentType", "application/octet-stream"),
            content_range=result.get("ContentRange"),
        )

    def delete(self, key: str) -> None:
        """Удалить только ключ попытки, переданный вызывающим кодом."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise StorageUnavailableError("S3 delete failed") from None


def build_storage(settings: Settings) -> S3Storage | None:
    """Создать клиент только при полной конфигурации.

    Учётные данные AWS из окружения и metadata, профиль и файлы ~/.aws не читаются.
    """
    values = (
        settings.s3_endpoint_url,
        settings.s3_access_key,
        settings.s3_secret_key,
        settings.s3_bucket,
        settings.s3_region,
    )
    if any(value is None or value == "" for value in values):
        return None

    endpoint, access_key, secret_key, bucket, region = values
    assert isinstance(endpoint, str)
    assert isinstance(access_key, str)
    assert isinstance(secret_key, SecretStr)
    assert isinstance(bucket, str)
    assert isinstance(region, str)
    session = boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key.get_secret_value(),
        region_name=region,
        botocore_session=botocore.session.Session(session_vars=_SESSION_VARS),
    )
    client = session.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=_SDK_CONFIG,
    )
    return S3Storage(client, bucket)
