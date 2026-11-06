"""Викладає RAG-застосунок на PythonAnywhere через API (без git і без консолі).

Що робить, по кроках:
1. Перевіряє, що є індекс data/chunks.json і всі потрібні файли.
2. Завантажує файли з MANIFEST у папку проєкту на PythonAnywhere.
3. Якщо задано OPENAI_API_KEY — записує його у .env на сервері
   (сам ключ у репозиторії НЕ зберігається, і в лог він не виводиться).
4. Видаляє на сервері файли, яких уже немає в репозиторії (крім .env).
5. Записує WSGI-файл і вказує веб-апці папку проєкту.
6. Перезапускає веб-апку і перевіряє /api/health.

Запуск:
- локально:   python scripts/deploy_pa.py            (бере .env)
- --dry-run   показати план без змін на сервері
- у GitHub Actions — той самий скрипт, налаштування з секретів репозиторію.

Потрібні змінні: PA_USERNAME, PA_API_TOKEN.
Необов'язкові:   PA_DOMAIN (за замовчуванням <user>.pythonanywhere.com),
                 PA_PROJECT_DIR (за замовчуванням /home/<user>/rag_site),
                 PA_VENV (за замовчуванням /home/<user>/.virtualenvs/lesson-helper),
                 PA_DATA_DIR (за замовчуванням /home/<user>/rag_data — БД, завантаження),
                 ADMIN_USERNAME, ADMIN_PASSWORD (адмінка /admin).
                 OPENAI_API_KEY, OPENAI_MODEL.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pa_api import PAError, PythonAnywhereClient  # noqa: E402
from rag.config import load_env_file  # noqa: E402
from rag.netutil import ssl_context  # noqa: E402

# Що саме їде на сервер. Тести, документація й папка data/source на сервер не потрібні.
MANIFEST_PATTERNS = [
    "app.py",
    "rag/*.py",
    "admin/*.py",
    "requirements.txt",   # для venv на сервері (див. docs/SETUP.md, крок 8а)
    "data/chunks.json",
    "data/source/*.html",   # потрібні, щоб адмінка могла перебудувати індекс на сервері
]
# Віртуальне середовище з FastAPI, яке вже є на PythonAnywhere (його не чіпаємо).
VENV_PROBE = "fastapi-0.116.1.dist-info"
TEMPLATE = ROOT / "deploy" / "pa_wsgi.py.tmpl"


def collect_local_files() -> dict:
    """Повертає {відносний_шлях: байти} для всіх файлів з MANIFEST."""
    files = {}
    for pattern in MANIFEST_PATTERNS:
        for match in sorted(glob.glob(str(ROOT / pattern))):
            path = Path(match)
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return files


def check_index() -> None:
    chunks = ROOT / "data" / "chunks.json"
    if not chunks.is_file():
        raise SystemExit("Немає data/chunks.json. Спершу: python -m rag.build")
    payload = json.loads(chunks.read_text(encoding="utf-8"))
    if not payload.get("chunks"):
        raise SystemExit("data/chunks.json порожній. Перебудуйте індекс: python -m rag.build")


def wait_for_health(domain: str, attempts: int = 12) -> bool:
    """Перевіряє /api/health після перезапуску. Повертає True, якщо все добре."""
    url = f"https://{domain}/api/health"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=15, context=ssl_context()) as response:
                data = json.loads(response.read().decode("utf-8"))
                if data.get("ok"):
                    print(f"Здоров'я: OK, фрагментів у пошуку: {data.get('chunks')}, "
                          f"модель увімкнена: {data.get('llm_enabled')}")
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        print(f"  чекаю сервер ({attempt}/{attempts})…")
        time.sleep(5)
    return False


def check_admin(domain: str) -> str:
    """Без пароля /admin має відповісти 401 (увімкнено) або 503 (вимкнено)."""
    try:
        urllib.request.urlopen(f"https://{domain}/admin", timeout=15, context=ssl_context())
        return "відповіла без входу — ПЕРЕВІРТЕ НАЛАШТУВАННЯ"
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return "працює (потрібен вхід)"
        return f"HTTP {exc.code} — дивіться Error log"
    except (urllib.error.URLError, OSError) as exc:
        return f"недоступна: {exc}"


def main() -> None:
    load_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Деплой RAG на PythonAnywhere")
    parser.add_argument("--dry-run", action="store_true",
                        help="показати план, нічого не змінювати на сервері")
    args = parser.parse_args()

    username = os.environ.get("PA_USERNAME", "").strip()
    token = os.environ.get("PA_API_TOKEN", "").strip()
    domain = os.environ.get("PA_DOMAIN", "").strip() or f"{username}.pythonanywhere.com"
    project_dir = os.environ.get("PA_PROJECT_DIR", "").strip() or f"/home/{username}/rag_site"
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4.1-mini"
    venv = os.environ.get("PA_VENV", "").strip() or f"/home/{username}/.virtualenvs/lesson-helper"
    data_dir = os.environ.get("PA_DATA_DIR", "").strip() or f"/home/{username}/rag_data"
    admin_user = os.environ.get("ADMIN_USERNAME", "").strip() or "admin"
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not admin_password:
        print("УВАГА: ADMIN_PASSWORD не задано — адмінка на сервері вимкнеться.")

    check_index()
    local_files = collect_local_files()
    print(f"Локальні файли для викладки: {len(local_files)}")
    for rel in sorted(local_files):
        print(f"  {rel}")
    print(f"Веб-апка: {domain}")
    print(f"Папка на сервері: {project_dir}")
    print(f"OpenAI ключ: {'буде записано в .env на сервері' if openai_key else 'не задано (працює режим без моделі)'}")
    print(f"Адмінка: https://{domain}/admin  (логін {admin_user})")
    print(f"Дані (БД, завантаження): {data_dir}  — деплой їх не чіпає")

    if args.dry_run:
        print("\n--dry-run: на сервері нічого не змінено.")
        return

    client = PythonAnywhereClient(username, token)
    try:
        client.check_token()
    except PAError as exc:
        raise SystemExit(f"Токен PythonAnywhere не працює: {exc}")

    # 1) Файли застосунку.
    for rel, content in local_files.items():
        client.upload(f"{project_dir}/{rel}", content)
        print(f"завантажено: {rel}")

    # 2) .env на сервері. Секрети не друкуємо.
    lines = [
        f"OPENAI_MODEL={openai_model}",
        f"RAG_DATA_DIR={data_dir}",
        f"ADMIN_USERNAME={admin_user}",
        f"ADMIN_PASSWORD={admin_password}",
    ]
    if openai_key:
        lines.insert(0, f"OPENAI_API_KEY={openai_key}")
    client.upload(f"{project_dir}/.env", ("\n".join(lines) + "\n").encode("utf-8"))
    print("записано: .env (на сервері)")

    # 3) Видаляємо на сервері зайві файли з попередніх викладок.
    remote_files, _ = client.walk(project_dir)
    wanted = {f"{project_dir}/{rel}" for rel in local_files}
    wanted.add(f"{project_dir}/.env")
    for remote in remote_files:
        if remote not in wanted and "/__pycache__/" not in remote:
            client.delete_file(remote)
            print(f"видалено зайвий файл на сервері: {remote}")

    # 4) WSGI-файл PythonAnywhere.
    wsgi_name = "/var/www/" + domain.replace(".", "_") + "_wsgi.py"
    wsgi_text = TEMPLATE.read_text(encoding="utf-8").replace("{project_dir}", project_dir)
    client.upload(wsgi_name, wsgi_text.encode("utf-8"))
    print(f"записано WSGI: {wsgi_name}")

    # 5) Веб-апка дивиться на нову папку і використовує Python 3.10.
    #    Застосунок не має сторонніх залежностей, тож віртуальне середовище не потрібне.
    client.patch_webapp(domain, {
        "source_directory": project_dir,
        "python_version": "3.10",
        "virtualenv_path": venv,
    })
    print(f"налаштовано веб-апку (venv: {venv})")
    try:
        if VENV_PROBE not in " ".join(client.list_dir(venv + "/lib/python3.10/site-packages/")):
            print("УВАГА: у venv немає FastAPI; адмінка буде недоступна, бот працюватиме.")
    except PAError as exc:
        print(f"УВАГА: не вдалося перевірити venv: {exc}")

    # 6) Перезапуск і перевірка.
    client.reload_webapp(domain)
    print("веб-апку перезапущено")
    if not wait_for_health(domain):
        raise SystemExit("Сервер не відповідає на /api/health. Дивіться Web → Error log на PythonAnywhere.")
    print(f"Адмінка: {check_admin(domain)}")
    print(f"Готово: https://{domain}/")


if __name__ == "__main__":
    try:
        main()
    except PAError as exc:
        raise SystemExit(f"Помилка PythonAnywhere API: {exc}")
