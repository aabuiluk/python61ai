"""Перевірка налаштувань крок за кроком. Нічого не змінює.

Запуск з кореня проєкту:
    python scripts/check_setup.py

Кожен рядок — одна перевірка: OK або ПОМИЛКА з підказкою, що зробити.
Скрипт можна запускати скільки завгодно разів.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pa_api import PAError, PythonAnywhereClient  # noqa: E402
from rag.config import load_env_file  # noqa: E402
from rag.netutil import ssl_context  # noqa: E402

results = []


def report(ok: bool, title: str, hint: str = "") -> None:
    status = "OK     " if ok else "ПОМИЛКА"
    line = f"[{status}] {title}"
    if not ok and hint:
        line += f"\n           → {hint}"
    print(line)
    results.append(ok)


def main() -> None:
    report(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (потрібен 3.10+)",
           "Встановіть новіший Python з python.org або через pyenv/brew")

    env_file = ROOT / ".env"
    report(env_file.is_file(), "Файл .env існує",
           "Скопіюйте .env.example у .env і заповніть значення (див. docs/SETUP.md, крок 2)")
    load_env_file(env_file)

    username = os.environ.get("PA_USERNAME", "").strip()
    token = os.environ.get("PA_API_TOKEN", "").strip()
    report(bool(username and token), "У .env є PA_USERNAME і PA_API_TOKEN",
           "Заповніть ці два поля в .env (токен: PythonAnywhere → Account → API token)")

    if username and token:
        try:
            PythonAnywhereClient(username, token).check_token()
            report(True, f"Токен PythonAnywhere працює (акаунт {username})")
        except PAError as exc:
            report(False, "Токен PythonAnywhere не приймається",
                   f"Перевірте токен і ім'я користувача. Деталі: {exc}")

    chunks = ROOT / "data" / "chunks.json"
    report(chunks.is_file(), "Індекс data/chunks.json є",
           "Побудуйте його: python -m rag.build")

    domain = os.environ.get("PA_DOMAIN", "").strip() or f"{username}.pythonanywhere.com"
    try:
        with urllib.request.urlopen(f"https://{domain}/api/health", timeout=15,
                                    context=ssl_context()) as response:
            # Перевіряємо саме наш застосунок, а не будь-яку сторінку на домені.
            data = json.loads(response.read().decode("utf-8"))
            report(bool(data.get("ok")), f"Сайт відповідає: https://{domain}/")
    except (urllib.error.URLError, OSError, ValueError):
        report(False, f"Сайт https://{domain}/ поки не відповідає",
               "Це нормально до першого деплою. Після деплою перевірте ще раз")

    report(shutil.which("git") is not None, "Встановлено git")
    report(shutil.which("gh") is not None, "Встановлено GitHub CLI (gh)",
           "Потрібно лише для секретів GitHub: brew install gh")
    if shutil.which("gh"):
        auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        report(auth.returncode == 0, "gh авторизовано в GitHub",
               "Виконайте: gh auth login (оберіть GitHub.com і HTTPS)")

    print()
    if all(results):
        print("Усі перевірки пройдено. Можна деплоїти: python scripts/deploy_pa.py")
    else:
        print("Є помилки. Виправте їх за підказками і запустіть цей скрипт ще раз.")


if __name__ == "__main__":
    main()
