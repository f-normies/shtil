"""Сессия пользователя нагрузочного теста (#41): вход по паролю, запросы с cookie, WebSocket.

Cookie передаётся заголовком явно: стенд ставит её с флагом Secure, и так поведение
не зависит от того, как клиент Locust хранит cookie.
"""

import json
import ssl
import time
from collections.abc import Callable

import gevent
import websocket
from accounts import PASSWORD
from locust import FastHttpUser, events

COOKIE = "compass_session"


def fire(name: str, started: float, error: Exception | None = None) -> None:
    """Записать в статистику Locust событие сокета: время в мс от `started`."""
    events.request.fire(
        request_type="WS",
        name=name,
        response_time=(time.perf_counter() - started) * 1000,
        response_length=0,
        exception=error,
        context={},
    )


class Session:
    """Вход по паролю, cookie сессии и WebSocket одного пользователя."""

    def __init__(self, user: FastHttpUser, email: str) -> None:
        self.user = user
        self.email = email
        self.token = ""
        self.me: dict = {}
        self.ws: websocket.WebSocket | None = None
        self.on_event: Callable[[dict], None] | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Cookie": f"{COOKIE}={self.token}"}

    def login(self) -> bool:
        with self.user.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": PASSWORD},
            name="POST /auth/login",
            catch_response=True,
        ) as resp:
            cookie = resp.headers.get("set-cookie") or ""
            if resp.status_code != 200 or f"{COOKIE}=" not in cookie:
                resp.failure(f"login {resp.status_code}")
                return False
            self.token = cookie.split(f"{COOKIE}=", 1)[1].split(";", 1)[0]
            self.me = resp.json()
        return True

    def get(self, path: str, name: str, **params: object) -> dict | list | None:
        with self.user.client.get(
            path,
            headers=self.headers,
            params=params or None,
            name=name,
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"{resp.status_code}")
                return None
            return resp.json()

    def post(
        self,
        path: str,
        name: str,
        body: dict | None = None,
        ok: tuple = (200, 201, 204),
    ):
        with self.user.client.post(
            path, headers=self.headers, json=body, name=name, catch_response=True
        ) as resp:
            if resp.status_code not in ok:
                resp.failure(f"{resp.status_code} {(resp.text or '')[:120]}")
                return None
            return resp.json() if resp.text else {}

    def connect(self) -> None:
        """Открыть сокет и читать события в отдельном greenlet, переподключаясь при обрыве."""
        gevent.spawn(self._loop)

    def _loop(self) -> None:
        # Схема сокета — по адресу стенда: локальный стенд работает по HTTP.
        scheme, host = self.user.host.split("://", 1)
        ws_scheme = "wss" if scheme == "https" else "ws"
        while self.user.running:
            started = time.perf_counter()
            try:
                self.ws = websocket.create_connection(
                    f"{ws_scheme}://{host}/api/v1/ws",
                    header=[f"Cookie: {COOKIE}={self.token}"],
                    origin=self.user.host,
                    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                    timeout=60,
                )
                fire("WS connect", started)
                self._read()
            except Exception as error:  # noqa: BLE001 - любая ошибка сокета идёт в статистику
                if not self.user.running:
                    return
                fire("WS dropped", started, error)
                gevent.sleep(5)
            finally:
                if self.ws is not None:
                    self.ws.close()
                    self.ws = None

    def _read(self) -> None:
        """Читать события, пока пользователь работает. Тишина в сокете — не обрыв:
        браузер держит соединение открытым, поэтому таймаут чтения не переподключает."""
        while self.user.running:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise ConnectionError("socket closed")
            if self.on_event is not None:
                self.on_event(json.loads(raw))

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
