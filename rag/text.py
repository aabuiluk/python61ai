"""Розбиття HTML-уроку на фрагменти (chunks) для пошуку.

Лише стандартна бібліотека: цей код має працювати на безплатному
PythonAnywhere без встановлення пакетів.

Ідея:
1. Читаємо HTML і ігноруємо службові частини (меню, стилі, SVG-схеми, кнопки).
2. Кожен заголовок h1/h2/h3 починає новий розділ. Запам'ятовуємо «шлях»
   заголовків (наприклад: "Пари 45–46 > Блок 5 > Завдання 5").
3. Текст розділу ріжемо на шматки приблизно по max_chars символів,
   з невеликим перекриттям, щоб не втратити контекст на межі.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from typing import Dict, List

# Теги, вміст яких повністю ігноруємо (меню, скрипти, стилі, векторні схеми).
SKIP_TAGS = {"head", "title", "script", "style", "svg", "nav", "button"}

# Теги, на межах яких завершуємо абзац.
BLOCK_TAGS = {
    "p", "li", "pre", "tr", "div", "table", "ol", "ul", "section",
    "details", "summary", "blockquote", "br",
}

HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3}


@dataclass
class Chunk:
    """Один фрагмент, який піде в пошук і в контекст для відповіді."""

    id: str        # стабільний ідентифікатор, напр. "c012"
    path: str      # шлях заголовків, напр. "Пари 45–46 > Блок 5"
    text: str      # власне текст фрагмента

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


def _collapse(text: str) -> str:
    """Прибирає зайві пробіли всередині рядка (для звичайного тексту)."""
    return re.sub(r"\s+", " ", text).strip()


class _LessonParser(HTMLParser):
    """Збирає «розділи» (заголовок-шлях + список рядків тексту)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0          # скільки SKIP-тегів ми зараз всередині
        self._in_pre = False          # у <pre> пробіли й переноси важливі
        self._heading_level = None    # 1..3, якщо зараз всередині h1/h2/h3
        self._heading_buf: List[str] = []
        self._text_buf: List[str] = []
        self._headings: List[str] = []  # поточний ланцюжок заголовків
        self.sections: List[dict] = []
        self._new_section(path="Вступ")

    # --- службові методи -------------------------------------------------

    def _new_section(self, path: str) -> None:
        self.sections.append({"path": path, "lines": []})

    def _flush(self) -> None:
        """Переносить накопичений текст у поточний розділ."""
        raw = "".join(self._text_buf)
        self._text_buf = []
        if self._in_pre:
            # Код зберігаємо як є, лише прибираємо крайні переноси.
            block = raw.strip("\n")
            if block.strip():
                self.sections[-1]["lines"].append("```\n" + block + "\n```")
            return
        line = _collapse(raw)
        if line:
            self.sections[-1]["lines"].append(line)

    def _close_heading(self) -> None:
        title = _collapse("".join(self._heading_buf))
        level = self._heading_level or 1
        self._heading_buf = []
        self._heading_level = None
        # Заголовок рівня N скидає все глибше (h2 скидає h3 і т.д.).
        self._headings = self._headings[: level - 1]
        if title:
            self._headings.append(title)
        self._new_section(path=" > ".join(self._headings) or "Без назви")

    # --- обробка HTML ----------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in HEADING_TAGS:
            self._flush()
            self._heading_level = HEADING_TAGS[tag]
            self._heading_buf = []
            return
        if tag == "pre":
            self._flush()
            self._in_pre = True
            return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in HEADING_TAGS and self._heading_level is not None:
            self._close_heading()
            return
        if tag == "pre":
            self._flush()
            self._in_pre = False
            return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._heading_level is not None:
            self._heading_buf.append(data)
        else:
            self._text_buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _split_long_line(line: str, max_chars: int) -> List[str]:
    """Ріже дуже довгий рядок (наприклад, великий блок коду) на частини."""
    if len(line) <= max_chars:
        return [line]
    return [line[i : i + max_chars] for i in range(0, len(line), max_chars)]


def _window(lines: List[str], max_chars: int, overlap: int) -> List[str]:
    """Збирає рядки у шматки до max_chars з перекриттям overlap символів."""
    pieces: List[str] = []
    for line in lines:
        pieces.extend(_split_long_line(line, max_chars))

    chunks: List[str] = []
    current: List[str] = []
    size = 0
    for piece in pieces:
        if current and size + len(piece) > max_chars:
            chunks.append("\n".join(current))
            # Беремо хвіст попереднього шматка як перекриття.
            tail: List[str] = []
            tail_size = 0
            for old in reversed(current):
                if tail_size + len(old) > overlap:
                    break
                tail.insert(0, old)
                tail_size += len(old)
            current = tail
            size = tail_size
        current.append(piece)
        size += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def parse_lesson_html(html_text: str, source_name: str = "lesson",
                      max_chars: int = 900, overlap: int = 150) -> List[Chunk]:
    """Перетворює HTML одного уроку на список Chunk.

    source_name попадає в id, щоб фрагменти з різних файлів не злипалися.
    """
    parser = _LessonParser()
    parser.feed(html_text)
    parser.close()

    chunks: List[Chunk] = []
    prefix = re.sub(r"[^a-z0-9]+", "", source_name.lower())[:12] or "src"
    for section in parser.sections:
        if not section["lines"]:
            continue
        for part in _window(section["lines"], max_chars, overlap):
            index = len(chunks)
            chunks.append(Chunk(id=f"{prefix}-{index:03d}",
                                path=section["path"], text=part))
    return chunks
