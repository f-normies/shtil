"""Ошибки публичных операций переписки."""


class MessagingError(Exception):
    """Ожидаемая ошибка чата с HTTP-статусом и устойчивой машинной меткой."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
