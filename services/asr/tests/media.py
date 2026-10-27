"""Сборка тестовых записей через PyAV: тесты кодируют звук сами, аудио в репозитории нет."""

import io
from fractions import Fraction

import av
import numpy as np
import numpy.typing as npt


def sine(seconds: float, rate: int, freq: float = 440.0) -> npt.NDArray[np.float32]:
    """Синус амплитудой 0,5."""
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def encode(
    samples: npt.NDArray[np.float32],
    *,
    rate: int,
    container: str,
    codec: str,
    channels: int = 1,
) -> bytes:
    """Кодирует моно-сигнал в контейнер; при channels=2 канал дублируется."""
    buf = io.BytesIO()
    layout = "stereo" if channels == 2 else "mono"
    with av.open(buf, mode="w", format=container) as out:
        stream = out.add_stream(codec, rate=rate, layout=layout)
        planes = np.tile(samples, (channels, 1))
        frame_size = stream.codec_context.frame_size or 1024
        pts = 0
        for start in range(0, planes.shape[1], frame_size):
            chunk = np.ascontiguousarray(planes[:, start : start + frame_size])
            frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout=layout)
            frame.sample_rate = rate
            frame.pts = pts
            frame.time_base = Fraction(1, rate)
            pts += chunk.shape[1]
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def encode_wav(samples: npt.NDArray[np.float32], *, rate: int) -> bytes:
    """WAV с PCM 16 бит: формат, который сервис не принимает."""
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="wav") as out:
        stream = out.add_stream("pcm_s16le", rate=rate, layout="mono")
        frame = av.AudioFrame.from_ndarray(
            (samples * 32767).astype(np.int16).reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = rate
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def encode_video_only() -> bytes:
    """webm только с видеодорожкой VP8, без звука."""
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="webm") as out:
        stream = out.add_stream("libvpx", rate=10)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        for i in range(3):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((16, 16, 3), dtype=np.uint8), format="rgb24"
            )
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def dominant_freq(samples: npt.NDArray[np.float32], rate: int) -> float:
    """Частота самого сильного пика спектра, Гц."""
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[int(np.argmax(spectrum))])
