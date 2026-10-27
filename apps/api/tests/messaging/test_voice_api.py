"""HTTP-контракты голосовых сообщений и доступного только через API аудио."""

from uuid import uuid4

import httpx

from tests.messaging.conftest import MakeMessagingCare, login


async def test_voice_upload_reports_missing_private_storage_as_503(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care("voice-doctor@example.com", "voice-patient@example.com")
    await login(client, care.patient.email)

    response = await client.post(
        f"/api/v1/patients/{care.patient.id}/messages/voice",
        data={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
        },
        files={"file": ("recording.webm", b"\x1aE\xdf\xa3audio", "audio/webm")},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "storage_unavailable"


async def test_voice_upload_rejects_unsupported_audio_mime(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-mime-doctor@example.com", "voice-mime-patient@example.com"
    )
    await login(client, care.patient.email)

    response = await client.post(
        f"/api/v1/patients/{care.patient.id}/messages/voice",
        data={
            "expected_doctor_id": str(care.doctor.id),
            "client_message_id": str(uuid4()),
        },
        files={"file": ("recording.bin", b"opaque", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_audio_type"


async def test_voice_upload_caps_chunked_body_before_multipart_parsing(
    client: httpx.AsyncClient,
    make_messaging_care: MakeMessagingCare,
) -> None:
    care = await make_messaging_care(
        "voice-size-doctor@example.com", "voice-size-patient@example.com"
    )
    await login(client, care.patient.email)
    boundary = "voice-body-boundary"
    prefix = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="expected_doctor_id"\r\n\r\n'
        f"{care.doctor.id}\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="client_message_id"\r\n\r\n'
        f"{uuid4()}\r\n"
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.webm"\r\n'
        "Content-Type: audio/webm\r\n\r\n"
    ).encode()

    async def upload_body():
        yield prefix
        for _ in range(27):
            yield b"a" * (1024 * 1024)

    response = await client.post(
        f"/api/v1/patients/{care.patient.id}/messages/voice",
        content=upload_body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "audio_too_large"
