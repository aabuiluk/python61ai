"""Читання налаштувань: змінні середовища і локальний файл .env.

Правила:
- .env лежить у корені проєкту, його НЕ комітимо (він є у .gitignore);
- змінні, які вже задані в середовищі (наприклад, у GitHub Actions),
  важливіші за .env і не перезаписуються;
- парсер свій, щоб не ставити python-dotenv (нічого зайвого на PythonAnywhere).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


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
    chunks_path: Path
    openai_api_key: str
    openai_model: str
    use_llm: bool
    top_k: int


def load_settings(env_file: Path | None = None) -> Settings:
    """Збирає налаштування з .env (якщо є) і з середовища."""
    load_env_file(env_file or (PROJECT_DIR / ".env"))

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    use_llm_flag = os.environ.get("RAG_USE_LLM", "1").strip() != "0"
    top_k_raw = os.environ.get("RAG_TOP_K", "4").strip()
    try:
        top_k = min(max(int(top_k_raw), 1), 10)
    except ValueError:
        top_k = 4

    return Settings(
        chunks_path=Path(os.environ.get("RAG_CHUNKS_PATH",
                                        str(PROJECT_DIR / "data" / "chunks.json"))),
        openai_api_key=api_key,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
        # LLM вмикається лише коли є ключ і він не вимкнений прапорцем.
        use_llm=bool(api_key) and use_llm_flag,
        top_k=top_k,
    )
