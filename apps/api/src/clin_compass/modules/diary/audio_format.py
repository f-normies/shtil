"""Проверка контейнера и дорожки аудиозаписей дневника."""

from io import BytesIO

import av

_FORMATS = {
    "audio/webm": ("webm", "opus"),
    "audio/mp4": ("mp4", "aac"),
}


def is_supported_audio(data: bytes, mime_type: str) -> bool:
    """Проверить контейнер и единственную аудиодорожку по фактическому содержимому."""
    expected = _FORMATS.get(mime_type)
    if expected is None:
        return False
    expected_container, expected_codec = expected
    try:
        with av.open(BytesIO(data), mode="r") as container:
            if expected_container not in container.format.name.split(","):
                return False
            streams = list(container.streams)
            return (
                len(streams) == 1
                and streams[0].type == "audio"
                and streams[0].codec_context.name == expected_codec
            )
    except av.FFmpegError:
        return False
