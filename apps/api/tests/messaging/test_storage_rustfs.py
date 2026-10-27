"""Настоящий S3 smoke-test: требуется только явный адрес закрытого RustFS."""

import os
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from clin_compass.core.config import Settings
from clin_compass.core.storage import StorageObjectMissingError, build_storage

AUDIO = b"\x1a\x45\xdf\xa3 synthetic RustFS test audio"


def test_rustfs_round_trips_private_audio_ranges_and_cleanup() -> None:
    endpoint = os.getenv("COMPASS_TEST_S3_ENDPOINT_URL")
    access_key = os.getenv("COMPASS_TEST_S3_ACCESS_KEY")
    secret_key = os.getenv("COMPASS_TEST_S3_SECRET_KEY")
    region = os.getenv("COMPASS_TEST_S3_REGION")
    if not any((endpoint, access_key, secret_key, region)):
        if os.getenv("CI", "").lower() == "true":
            pytest.fail("CI must provide the isolated RustFS test endpoint and credentials")
        pytest.skip("RustFS integration variables are not configured")
    if not all((endpoint, access_key, secret_key, region)):
        pytest.fail("RustFS integration requires endpoint, access key, secret key and region")

    settings = Settings(
        database_url="postgresql+asyncpg://unused/unused",
        s3_endpoint_url=endpoint,
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_bucket=f"compass-test-{uuid4().hex[:20]}",
        s3_region=region,
    )
    storage = build_storage(settings)
    assert storage is not None
    bucket = settings.s3_bucket
    assert bucket is not None
    client = storage._client
    key = f"messages/{uuid4()}"
    client.create_bucket(Bucket=bucket)
    try:
        storage.put(key, AUDIO, "audio/webm")

        complete = storage.get(key)
        try:
            assert complete.body.read() == AUDIO
            assert complete.size == len(AUDIO)
            assert complete.content_type == "audio/webm"
        finally:
            complete.body.close()

        ranged = storage.get(key, byte_range="bytes=2-8")
        try:
            assert ranged.body.read() == AUDIO[2:9]
            assert ranged.size == 7
            assert ranged.content_range == f"bytes 2-8/{len(AUDIO)}"
        finally:
            ranged.body.close()

        storage.delete(key)
        with pytest.raises(StorageObjectMissingError):
            storage.get(key)
    finally:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {"NoSuchKey", "404", "NotFound"}:
                raise
        client.delete_bucket(Bucket=bucket)
        client.close()
