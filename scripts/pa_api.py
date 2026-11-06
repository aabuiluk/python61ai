"""Мінімальний клієнт PythonAnywhere API (лише стандартна бібліотека).

Документація API: https://help.pythonanywhere.com/pages/API/

Ця бібліотека не запускається сама. Її використовують інші скрипти:
- scripts/check_setup.py          — перевірка налаштувань;
- scripts/pa_clear_old_site.py    — прибирання старого сайту;
- scripts/deploy_pa.py            — викладка застосунку.

Токен береться з PA_API_TOKEN. Пароль від сайту PythonAnywhere НЕ потрібен:
API працює тільки з токеном, а він видається на вкладці «Account».
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, List, Optional

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from rag.netutil import ssl_context  # noqa: E402

API_ROOT = "https://www.pythonanywhere.com/api/v0/user/{user}"


class PAError(RuntimeError):
    """Помилка виклику PythonAnywhere API. Токен у повідомленні не виводимо."""


class PythonAnywhereClient:
    def __init__(self, username: str, token: str, timeout: int = 30) -> None:
        if not username or not token:
            raise PAError("Потрібні PA_USERNAME і PA_API_TOKEN")
        self.username = username
        self._token = token
        self._timeout = timeout
        self._base = API_ROOT.format(user=urllib.parse.quote(username))

    # --- низькорівневий запит ---------------------------------------------

    def _request(self, method: str, url_path: str, body: Optional[bytes] = None,
                 headers: Optional[Dict[str, str]] = None,
                 expect_json: bool = True):
        request_headers = {"Authorization": f"Token {self._token}"}
        request_headers.update(headers or {})
        request = urllib.request.Request(self._base + url_path, data=body,
                                         method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout,
                                          context=ssl_context()) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise PAError(f"{method} {url_path} -> HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PAError(f"{method} {url_path} -> мережева помилка: {exc}") from None

        if not expect_json or not raw:
            return status, raw
        try:
            return status, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return status, raw

    def _json(self, method: str, url_path: str, payload: Optional[dict] = None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        _, data = self._request(method, url_path, body, headers)
        return data

    # --- акаунт -------------------------------------------------------------

    def check_token(self) -> dict:
        """GET /cpu/ — найпростіший виклик, який перевіряє токен і ім'я."""
        return self._json("GET", "/cpu/")

    # --- файли -----------------------------------------------------------

    def list_tree(self, path: str) -> List[str]:
        """Рекурсивний список шляхів у папці (каталоги закінчуються на '/')."""
        quoted = urllib.parse.quote(path)
        data = self._json("GET", f"/files/tree/?path={quoted}")
        return list(data) if isinstance(data, list) else []

    def walk(self, folder: str):
        """Обходить папку рекурсивно. Повертає (файли, папки) як повні шляхи.

        Список tree/ на PythonAnywhere не є повним для глибоких папок, тому
        ходимо по дереву самі через GET /files/path (по одній папці за раз).
        """
        files: List[str] = []
        dirs: List[str] = []
        queue = [folder.rstrip("/") + "/"]
        while queue:
            current = queue.pop()
            for name, kind in self.list_dir(current).items():
                full = current + name
                if kind == "directory":
                    dirs.append(full + "/")
                    queue.append(full + "/")
                else:
                    files.append(full)
        return files, dirs

    def list_dir(self, path: str) -> Dict[str, str]:
        """Вміст однієї папки: {ім'я: 'file'|'directory'}."""
        data = self._json("GET", f"/files/path{urllib.parse.quote(path)}")
        if isinstance(data, dict):
            return {name: info.get("type", "") for name, info in data.items()}
        return {}

    def upload(self, remote_path: str, content: bytes) -> int:
        """Записує файл на сервер (створює папки, перезаписує існуючі)."""
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="content"; filename="upload"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        status, _ = self._request(
            "POST", f"/files/path{urllib.parse.quote(remote_path)}", body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            expect_json=False,
        )
        return status

    def delete_file(self, remote_path: str) -> None:
        """Видаляє один файл. Для папок API не документує видалення."""
        self._request("DELETE", f"/files/path{urllib.parse.quote(remote_path)}",
                      expect_json=False)

    # --- веб-застосунок ---------------------------------------------------

    def get_webapp(self, domain: str) -> dict:
        return self._json("GET", f"/webapps/{domain}/")

    def patch_webapp(self, domain: str, fields: dict) -> dict:
        return self._json("PATCH", f"/webapps/{domain}/", fields)

    def reload_webapp(self, domain: str) -> None:
        self._request("POST", f"/webapps/{domain}/reload/", expect_json=False)
