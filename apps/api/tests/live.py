"""Клиент к настоящему uvicorn из фикстуры `live_server`: вход, WebSocket, чтение событий."""

import asyncio
import json

import httpx
from websockets.asyncio.client import ClientConnection, connect

PASSWORD = "correct horse battery staple"


async def session_cookie(host: str, email: str) -> str:
    async with httpx.AsyncClient(base_url=f"http://{host}") as client:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200
        return f"compass_session={client.cookies['compass_session']}"


def ws_connect(host: str, cookie: str | None, origin: str | None = None) -> connect:
    headers = {}
    if cookie:
        headers["Cookie"] = cookie
    headers["Origin"] = origin or f"http://{host}"
    return connect(f"ws://{host}/api/v1/ws", additional_headers=headers)


async def receive(ws: ClientConnection) -> dict[str, object]:
    message = await asyncio.wait_for(ws.recv(), timeout=5)
    parsed: dict[str, object] = json.loads(message)
    return parsed
