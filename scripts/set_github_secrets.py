"""Передає секрети з локального .env у GitHub Actions secrets через gh CLI.

Чому так: секрети НЕ лежать у файлах репозиторію. Вони живуть у двох місцях:
- у вашому локальному .env (тільки на вашому комп'ютері);
- у GitHub → Settings → Secrets and variables → Actions (зашифровано GitHub).
Цей скрипт копіює значення з першого у друге. Значення передаються через
stdin, тож не потрапляють ні в командний рядок, ні в історію терміналу.

Запуск з кореня проєкту:
    python scripts/set_github_secrets.py --dry-run   # лише перелік імен
    python scripts/set_github_secrets.py             # записати секрети

Вимоги:
- встановлений GitHub CLI (gh) і виконаний вхід: gh auth login
- у .env заповнено PA_USERNAME і PA_API_TOKEN
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.config import load_env_file, shared_env_file  # noqa: E402

# Імена, які піднімаємо в GitHub. Порожні значення пропускаються.
SECRET_NAMES = [
    "PA_USERNAME",
    "PA_API_TOKEN",
    "PA_DOMAIN",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
]


def repo_from_origin() -> str:
    """Визначає owner/repo з remote origin поточного клону."""
    result = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                            cwd=ROOT, capture_output=True, text=True)
    url = result.stdout.strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url)
    if not match:
        raise SystemExit("Не бачу remote origin на GitHub. Вкажіть --repo owner/name")
    return f"{match.group(1)}/{match.group(2)}"


def main() -> None:
    load_env_file(shared_env_file())
    parser = argparse.ArgumentParser(description="Секрети .env -> GitHub Actions")
    parser.add_argument("--repo", default=None, help="owner/name (за замовчуванням з origin)")
    parser.add_argument("--dry-run", action="store_true", help="лише показати імена")
    args = parser.parse_args()

    values = {name: os.environ.get(name, "").strip() for name in SECRET_NAMES}
    to_set = [name for name, value in values.items() if value]
    if not values["ADMIN_PASSWORD"]:
        print("УВАГА: ADMIN_PASSWORD порожній — адмінка на сервері буде вимкнена.")
    if not values["PA_USERNAME"] or not values["PA_API_TOKEN"]:
        raise SystemExit("У .env мають бути PA_USERNAME і PA_API_TOKEN")

    repo = args.repo or repo_from_origin()
    print(f"Репозиторій: {repo}")
    print("Буде передано секрети:", ", ".join(to_set))
    if args.dry_run:
        print("--dry-run: нічого не передано.")
        return

    if not shutil.which("gh"):
        raise SystemExit("Не знайдено gh. Встановіть: brew install gh, потім gh auth login")
    auth = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                          capture_output=True, text=True)
    if auth.returncode != 0:
        raise SystemExit("gh не авторизовано. Виконайте: gh auth login  і повторіть запуск")

    for name in to_set:
        # Значення йде через stdin, а не через аргумент командного рядка.
        result = subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo],
            input=values[name].encode("utf-8"), capture_output=True,
        )
        if result.returncode != 0:
            # stderr від gh не містить значення секрету, але обрізаємо для безпеки.
            message = result.stderr.decode("utf-8", "replace")[:200]
            raise SystemExit(f"Не вдалося записати {name}: {message}")
        print(f"записано секрет: {name}")
    print("Готово. Секрети зберігаються в GitHub, а не в файлах репозиторію.")


if __name__ == "__main__":
    main()
