"""Читання налаштувань: змінні середовища і локальний файл .env.

Правила:
- .env лежить у корені проєкту, його НЕ комітимо (він є у .gitignore);
- змінні, які вже задані в середовищі (наприклад, у GitHub Actions),
  важливіші за .env і не перезаписуються;
- парсер свій, щоб не ставити python-dotenv (нічого зайвого на PythonAnywhere).

Дані, які змінюються під час роботи сайту (БД статистики, завантажені
файли, зміни налаштувань з адмінки), лежать ПОЗА папкою проєкту — у
RAG_DATA_DIR. Тоді деплой їх не стирає.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent


def shared_env_file() -> Path:
    """Єдиний .env для всіх копій репозиторію (основної і git worktree).

    Git worktree (папка гілки в .claude/worktrees/...) має свій корінь, тож
    .env у ньому відрізняється від .env основної папки. Тому беремо .env
    з кореня ОСНОВНОГО репозиторію (git common dir). Якщо його немає —
    використовуємо .env поруч із кодом.
    """
    import subprocess

    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=PROJECT_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        main_env = Path(common).parent / ".env"
        if main_env.is_file():
            return main_env
    except (OSError, subprocess.CalledProcessError):
        pass
    return PROJECT_DIR / ".env"


def load_env_file(path: Path) -> int:
    """Завантажує пари KEY=VALUE з файлу в os.environ. Повертає кількість."""
    if not path.is_file():
        return 0
    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Прибираємо лапки: KEY="value" або KEY='value'.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


@dataclass(frozen=True)
class Settings:
    """Налаштування за замовчуванням (з .env/середовища).

    Частину з них адміністратор може змінити в адмінці; тоді діє значення з БД.
    """

    repo_chunks_path: Path      # data/chunks.json у репозиторії
    repo_source_dir: Path      # data/source — HTML-уроки
    data_dir: Path             # RAG_DATA_DIR: БД, завантаження, runtime-індекс
    openai_api_key: str
    openai_model: str
    use_llm: bool              # ключ є і прапорець RAG_USE_LLM не вимкнено
    top_k: int
    llm_per_minute: int
    admin_username: str
    admin_password: str


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return min(max(int(raw), low), high)
    except ValueError:
        return default


def load_settings(env_file: Optional[Path] = None) -> Settings:
    """Збирає налаштування з .env (якщо є) і з середовища."""
    load_env_file(env_file or (PROJECT_DIR / ".env"))

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    use_llm_flag = os.environ.get("RAG_USE_LLM", "1").strip() != "0"
    data_dir = Path(os.environ.get("RAG_DATA_DIR", "").strip()
                    or str(PROJECT_DIR / "runtime_data"))

    return Settings(
        repo_chunks_path=PROJECT_DIR / "data" / "chunks.json",
        repo_source_dir=PROJECT_DIR / "data" / "source",
        data_dir=data_dir,
        openai_api_key=api_key,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
        use_llm=bool(api_key) and use_llm_flag,
        top_k=_int_env("RAG_TOP_K", 4, 1, 10),
        llm_per_minute=_int_env("RAG_LLM_PER_MINUTE", 20, 1, 1000),
        admin_username=os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin",
        admin_password=os.environ.get("ADMIN_PASSWORD", "").strip(),
    )
