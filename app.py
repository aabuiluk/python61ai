"""Точка входу для PythonAnywhere (і для локального запуску).

PythonAnywhere-файл WSGI (генерується scripts/deploy_pa.py) робить:
    from app import application
Тому ім'я змінної має бути саме `application`.

Локальний запуск (без сторонніх пакетів):
    python app.py            -> http://127.0.0.1:8000
    python app.py 5001       -> інший порт
"""

from __future__ import annotations

import sys
from wsgiref.simple_server import make_server

from rag.web import create_app

application = create_app()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    with make_server("127.0.0.1", port, application) as server:
        print(f"RAG працює: http://127.0.0.1:{port}  (Ctrl+C — зупинити)")
        server.serve_forever()
