"""Настройки сервиса из переменных окружения с префиксом ASR_. Их задаёт services/asr/serve.sh."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Неизменяемые настройки. Параметры распознавания — константы со ссылкой на ADR-0007."""

    model_config = SettingsConfigDict(env_prefix="ASR_", frozen=True, extra="ignore")

    # Каталог с моделью, bpe.vocab и silero_vad.onnx, который готовит fetch.sh.
    model_dir: Path
    # Потоки ONNX Runtime на распознаватель. На M3 Мака 18 ГБ ядер меньше, чем на M3 Max.
    num_threads: int = 4
