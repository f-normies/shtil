"""Ошибки дневника с устойчивыми HTTP-кодами."""


class DiaryError(Exception):
    """Ожидаемая ошибка команды или чтения дневника."""

    def __init__(self, status: int, code: str, detail: str, **fields: object) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.fields = fields
