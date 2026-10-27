"""HTTP validation for actual diary audio tracks rather than container signatures."""

import httpx
import pytest
from fastapi import FastAPI

from tests.diary.conftest import DiaryCare, login
from tests.diary.voice_support import MP4_ALAC, WEBM, MemoryStorage, upload_voice, voice_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audio", "content_type"),
    [
        (WEBM.replace(b"A_OPUS", b"A_FLAC"), "audio/webm"),
        (MP4_ALAC, "audio/mp4"),
        (WEBM[:4], "audio/webm"),
        (b"\x00\x00\x00\x10ftypisom", "audio/mp4"),
    ],
    ids=["webm-flac-track", "mp4-alac-track", "webm-signature-only", "mp4-ftyp-only"],
)
async def test_voice_upload_rejects_wrong_codec_and_truncated_container(
    app: FastAPI,
    client: httpx.AsyncClient,
    diary_care: DiaryCare,
    audio: bytes,
    content_type: str,
) -> None:
    storage = MemoryStorage()
    app.state.storage = storage
    await login(client, diary_care.patient.email)

    response = await upload_voice(client, voice_metadata(), audio=audio, content_type=content_type)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_audio"
    assert storage.put_calls == []
