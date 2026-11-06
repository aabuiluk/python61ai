"""Спільний стан застосунку: сховище, індекс фрагментів і робочі налаштування.

Його використовують і публічний бот (rag/web.py), і адмінка (admin/).
Один процес — одна копія стану. Адмінка змінює налаштування в БД і
перебудовує індекс; бот бере нові значення при наступному запиті.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from rag.build import chunks_from_files, write_chunks
from rag.config import Settings, load_settings
from rag.search import BM25Index
from rag.store import Store
from rag.text import Chunk

# Налаштування, які адмін може міняти. Значення з БД мають пріоритет над .env.
EDITABLE_KEYS = ("top_k", "use_llm", "openai_model", "llm_per_minute", "public_enabled")


def load_chunks(path: Path) -> List[Chunk]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Chunk(id=item["id"], path=item["path"], text=item["text"])
            for item in payload["chunks"]]


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.data_dir = settings.data_dir
        self.sources_dir = self.data_dir / "sources"          # завантажені адміном HTML
        self.runtime_chunks = self.data_dir / "chunks.json"   # індекс, побудований на сервері
        self.store = Store(self.data_dir / "rag.sqlite3")
        self._index_lock = threading.Lock()
        self._index: BM25Index = BM25Index([])
        self.chunk_count = 0
        self.reload_index()

    # --- індекс --------------------------------------------------------------

    def reload_index(self) -> None:
        """Завантажує індекс: спершу з runtime-файлу, інакше з репозиторію."""
        path = self.runtime_chunks if self.runtime_chunks.is_file() else self.settings.repo_chunks_path
        chunks = load_chunks(path)
        with self._index_lock:
            self._index = BM25Index(chunks)
            self.chunk_count = len(chunks)

    def index(self) -> BM25Index:
        with self._index_lock:
            return self._index

    def source_files(self) -> Dict[str, Path]:
        """Усі HTML-уроки: з репозиторію, а завантажені адміном замінюють однойменні."""
        files: Dict[str, Path] = {}
        repo = self.settings.repo_source_dir
        if repo.is_dir():
            for path in sorted(repo.glob("*.html")):
                files[path.name] = path
        if self.sources_dir.is_dir():
            for path in sorted(self.sources_dir.glob("*.html")):
                files[path.name] = path
        return files

    def rebuild_index(self) -> int:
        """Перебудовує індекс з усіх джерел і одразу застосовує його."""
        files = self.source_files()
        chunks = chunks_from_files(str(p) for p in files.values())
        count = write_chunks(chunks, sorted(files), self.runtime_chunks)
        self.reload_index()
        return count

    # --- налаштування --------------------------------------------------------

    def effective(self) -> Dict[str, object]:
        """Діючі значення: з БД, а якщо там нема — з .env (і стандартні)."""
        s = self.settings
        stored = self.store.get_settings()
        use_llm = bool(stored.get("use_llm", s.use_llm)) and bool(s.openai_api_key)
        return {
            "top_k": int(stored.get("top_k", s.top_k)),
            "use_llm": use_llm,
            "llm_available": bool(s.openai_api_key),
            "openai_model": str(stored.get("openai_model", s.openai_model)),
            "llm_per_minute": int(stored.get("llm_per_minute", s.llm_per_minute)),
            "public_enabled": bool(stored.get("public_enabled", True)),
        }


_runtime: Optional[Runtime] = None
_runtime_lock = threading.Lock()


def get_runtime() -> Runtime:
    """Один Runtime на процес. Створюється при першому виклику."""
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = Runtime(load_settings())
        return _runtime
