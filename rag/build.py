"""Збирає фрагменти з HTML-уроків (data/chunks.json або runtime-індекс).

Запуск з кореня проєкту:

    python -m rag.build

Або з власними файлами:

    python -m rag.build --source data/source/урок.html --out data/chunks.json

Результат (chunks.json) комітиться в репозиторій. Сервер на PythonAnywhere
читає готовий файл; адмінка може перебудувати індекс прямо на сервері
(функція rebuild_index у rag/runtime.py) з тих самих HTML-файлів.
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Dict, Iterable, List

from rag.config import PROJECT_DIR
from rag.text import parse_lesson_html


def chunks_from_files(source_files: Iterable[str]) -> List[Dict[str, str]]:
    """Читає HTML-файли і повертає список фрагментів у вигляді словників."""
    all_chunks: List[Dict[str, str]] = []
    for source in source_files:
        path = Path(source)
        html_text = path.read_text(encoding="utf-8")
        chunks = parse_lesson_html(html_text, source_name=path.stem)
        all_chunks.extend(chunk.as_dict() for chunk in chunks)
    return all_chunks


def write_chunks(chunks: List[Dict[str, str]], source_names: List[str], out_path: Path) -> int:
    """Записує JSON-індекс. Формат детермінований, щоб CI міг порівняти файли."""
    if not chunks:
        raise ValueError("Не знайдено жодного фрагмента. Перевірте HTML-файли.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_files": source_names,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    # ensure_ascii=False — щоб український текст був читабельним у файлі.
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return len(chunks)


def build_chunks(source_files, out_path: Path) -> int:
    """Читає HTML-файли, пише JSON і повертає кількість фрагментів."""
    files = [str(f) for f in source_files]
    chunks = chunks_from_files(files)
    return write_chunks(chunks, [Path(f).name for f in files], out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Побудувати індекс фрагментів уроку")
    parser.add_argument(
        "--source", nargs="*",
        default=[str(PROJECT_DIR / "data" / "source" / "*.html")],
        help="HTML-файли або шаблони (за замовчуванням data/source/*.html)",
    )
    parser.add_argument(
        "--out", default=str(PROJECT_DIR / "data" / "chunks.json"),
        help="куди записати JSON (за замовчуванням data/chunks.json)",
    )
    args = parser.parse_args()

    files = []
    for pattern in args.source:
        matches = sorted(glob.glob(pattern))
        files.extend(matches if matches else [pattern])
    count = build_chunks(files, Path(args.out))
    print(f"Готово: {count} фрагментів -> {args.out}")


if __name__ == "__main__":
    main()
