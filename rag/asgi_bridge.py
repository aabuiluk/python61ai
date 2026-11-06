"""ASGI → WSGI без потоків: запускає FastAPI-застосунок в одному потоці.

Чому не a2wsgi: вона створює власні потоки і чекає їх через threading.Event.
На PythonAnywhere (uWSGI без enable-threads) це призводить до зависання
запиту й усього сайту. Тут кожен запит виконується в поточному потоці
через asyncio-цикл, який закривається після відповіді.

Обмеження: відповідь збирається повністю (без стрімінгу). Для адмінки
це не проблема.
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Callable


def asgi_to_wsgi(app) -> Callable:
    def wsgi_app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "") or "/"
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        body = environ["wsgi.input"].read(length) if length > 0 else b""

        headers = []
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-").lower()
                headers.append((name.encode("latin-1"), value.encode("latin-1")))
        if environ.get("CONTENT_TYPE"):
            headers.append((b"content-type", environ["CONTENT_TYPE"].encode("latin-1")))
        if length:
            headers.append((b"content-length", str(length).encode("latin-1")))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": environ.get("wsgi.url_scheme", "https"),
            "path": path,
            "raw_path": path.encode("utf-8"),
            "root_path": "",
            "query_string": environ.get("QUERY_STRING", "").encode("latin-1"),
            "headers": headers,
            "server": (environ.get("SERVER_NAME", "localhost"),
                       int(environ.get("SERVER_PORT", 443) or 443)),
            "client": None,
        }

        async def receive():
            # Тіло відправлено один раз; далі клієнт «відключився».
            if not receive.sent:
                receive.sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}
        receive.sent = False

        collected = {"status": 500, "headers": [], "body": []}

        async def send(message):
            if message["type"] == "http.response.start":
                collected["status"] = message["status"]
                collected["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                collected["body"].append(message.get("body", b""))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(app(scope, receive, send))
        finally:
            loop.close()

        status = collected["status"]
        reason = HTTPStatus(status).phrase
        response_headers = [(k.decode("latin-1"), v.decode("latin-1"))
                            for k, v in collected["headers"]]
        start_response(f"{status} {reason}", response_headers)
        return [b"".join(collected["body"])]

    return wsgi_app
