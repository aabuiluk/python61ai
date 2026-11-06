"""Видаляє СТАРИЙ сайт з PythonAnywhere (файли папки lesson_helper).

Що робить:
1. Показує, що буде видалено (без змін — це режим за замовчуванням).
2. З прапорцем --yes видаляє всі файли вказаної папки, а потім порожні папки.

Що НЕ чіпає:
- домашню папку (.bashrc, .virtualenvs, README.txt тощо);
- віртуальні середовища;
- саму веб-апку (її налаштовує deploy_pa.py — вона залишається, змінюється лише код).

Запуск:
    python scripts/pa_clear_old_site.py                 # перевірка, нічого не видаляє
    python scripts/pa_clear_old_site.py --yes           # справжнє видалення

Потрібні змінні PA_USERNAME і PA_API_TOKEN (з .env або з середовища).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pa_api import PAError, PythonAnywhereClient  # noqa: E402
from rag.config import load_env_file, shared_env_file  # noqa: E402

# Файли поза папкою, які входили в старий сайт (знайдено в домашній папці).
EXTRA_OLD_FILES = ["lesson_helper_extract.sh"]


def main() -> None:
    load_env_file(shared_env_file())
    parser = argparse.ArgumentParser(description="Прибирання старого сайту на PythonAnywhere")
    parser.add_argument("--yes", action="store_true",
                        help="справді видалити (без цього прапорця — лише перелік)")
    parser.add_argument("--folder", default=None,
                        help="папка старого сайту (за замовчуванням /home/<user>/lesson_helper)")
    args = parser.parse_args()

    username = os.environ.get("PA_USERNAME", "").strip()
    token = os.environ.get("PA_API_TOKEN", "").strip()
    client = PythonAnywhereClient(username, token)

    home = f"/home/{username}"
    folder = (args.folder or f"{home}/lesson_helper").rstrip("/") + "/"
    # Захист від випадкового видалення всієї домашньої папки.
    if not folder.startswith(home + "/") or folder == home + "/":
        raise SystemExit(f"Відмова: папка {folder} має бути всередині {home}/ і не дорівнювати їй")

    try:
        top = client.list_dir(folder)
    except PAError as exc:
        raise SystemExit(f"Не вдалося прочитати {folder}: {exc}")

    print(f"Папка: {folder}  (буде видалено разом із усім вмістом)")
    for name, kind in sorted(top.items()):
        print(f"  {'папка' if kind == 'directory' else 'файл '}  {name}")
    for name in EXTRA_OLD_FILES:
        print(f"  файл   {home}/{name}")

    if not args.yes:
        print("\nНічого не видалено. Щоб видалити, запустіть з прапорцем --yes.")
        return

    # API PythonAnywhere видаляє непорожню папку разом із вмістом (перевірено).
    try:
        client.delete_file(folder.rstrip("/"))
        print(f"видалено папку: {folder}")
    except PAError as exc:
        raise SystemExit(f"Не вдалося видалити {folder}: {exc}")

    for name in EXTRA_OLD_FILES:
        path = f"{home}/{name}"
        try:
            client.delete_file(path)
            print(f"видалено файл: {path}")
        except PAError as exc:
            # Файлу може вже не бути — це не помилка.
            print(f"пропущено {path}: {exc}")

    print("Готово: старий сайт видалено.")


if __name__ == "__main__":
    main()
