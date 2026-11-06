"""Точка входу для PythonAnywhere (і для локального запуску).

PythonAnywhere (через deploy/pa_wsgi.py.tmpl) шукає змінну `application`.

Диспетчер:
- /admin и /admin/...  -> адмінка (FastAPI через rag/asgi_bridge.py), якщо її вдалося підняти;
- усе інше              -> публічний бот.
Якщо FastAPI не встановлено, /admin відповідає 503, а бот працює далі.

Локальний запуск:
    python app.py            -> http://127.0.0.1:8000  (бот і /admin)
    python app.py 5001       -> інший порт
"""

from __future__ import annotations

import sys
from wsgiref.simple_server import make_server

from rag.runtime import get_runtime
from rag.web import create_app

runtime = get_runtime()
_public_app = create_app(runtime)

_admin_app = None
_admin_error = ""
try:
    from admin.app import build_admin
    from rag.asgi_bridge import asgi_to_wsgi

    _admin_app = asgi_to_wsgi(build_admin(runtime))
except Exception as exc:  # noqa: BLE001 — адмінка не має ламати бота
    _admin_error = f"Адмінку не запущено: {exc.__class__.__name__}. Перевірте venv на сервері."


def application(environ, start_response):
    path = environ.get("PATH_INFO", "") or "/"
    if path == "/admin" or path.startswith("/admin/"):
        if _admin_app is not None:
            return _admin_app(environ, start_response)
        body = _admin_error.encode("utf-8")
        start_response("503 Service Unavailable", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]
    return _public_app(environ, start_response)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    with make_server("127.0.0.1", port, application) as server:
        print(f"Бот: http://127.0.0.1:{port}   Адмінка: http://127.0.0.1:{port}/admin")
        server.serve_forever()
