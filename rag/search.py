"""Пошук за BM25 по фрагментах уроку (лише стандартна бібліотека).

Чому BM25, а не embeddings:
- не потребує ключа API і виклику мережі для кожного питання;
- працює на безплатному PythonAnywhere, де зовнішні сайти часто заблоковані;
- для одного уроку (кілька сотень фрагментів) точності достатньо.

Як це працює (коротко):
- текст переводимо в «токени» (слова у нижньому регістрі без стоп-слів);
- слова довші за 5 символів скорочуємо до перших 5 літер. Це грубий стемінг:
  «заявка», «заявки», «заявку» стають однаковими токенами. Для української
  мови це добре працює без словників;
- BM25 рахує, наскільки фрагмент схожий на питання, з урахуванням того,
  що рідкісні слова важливіші за часті.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Sequence

from rag.text import Chunk

# Коротке стоп-слово-список (українська + англійська). Потрібен, щоб
# «і», «як», «the» не домінували в рейтингу.
STOPWORDS = {
    "і", "й", "та", "або", "але", "в", "у", "на", "з", "із", "до", "що",
    "як", "це", "для", "не", "за", "по", "від", "при", "ми", "ви", "він",
    "вона", "вони", "я", "ти", "чи", "бо", "так", "ні", "про", "між",
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "is", "are",
    "with", "for", "by", "as", "it", "this", "that", "be",
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)
K1 = 1.5   # насиченість частоти слова
B = 0.75   # нормалізація за довжиною фрагмента


def tokenize(text: str) -> List[str]:
    """Розбиває текст на нормалізовані токени."""
    tokens: List[str] = []
    for word in _WORD_RE.findall(text.lower()):
        if word in STOPWORDS:
            continue
        if len(word) < 2 and not word.isdigit():
            continue
        # Слова з цифрами та "_" (T101, lookup_ticket) не скорочуємо:
        # там важлива точна форма.
        if len(word) > 5 and word.isalpha():
            word = word[:5]
        tokens.append(word)
    return tokens


class BM25Index:
    """Індекс у пам'яті, який будується один раз при старті застосунку."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = list(chunks)
        self._doc_tf: List[Counter] = []
        self._doc_len: List[int] = []
        self._df: Counter = Counter()

        for chunk in self.chunks:
            # Шлях заголовків теж індексуємо: питання «Блок 5» має знаходити блок.
            tokens = tokenize(chunk.path + " " + chunk.text)
            tf = Counter(tokens)
            self._doc_tf.append(tf)
            self._doc_len.append(len(tokens))
            self._df.update(tf.keys())

        n = len(self.chunks)
        self._avgdl = (sum(self._doc_len) / n) if n else 0.0
        self._n = n

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        # Формула BM25 з додатним значенням навіть для дуже частих слів.
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 4) -> List[Dict[str, object]]:
        """Повертає до top_k фрагментів з оцінкою релевантності (score > 0)."""
        terms = tokenize(query)
        if not terms or not self._n:
            return []

        scored: List[tuple] = []
        for i, tf in enumerate(self._doc_tf):
            score = 0.0
            length_norm = 1 - B + B * (self._doc_len[i] / (self._avgdl or 1))
            for term in set(terms):
                freq = tf.get(term, 0)
                if not freq:
                    continue
                score += self._idf(term) * (freq * (K1 + 1)) / (freq + K1 * length_norm)
            if score > 0:
                scored.append((score, i))

        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, i in scored[: max(1, top_k)]:
            chunk = self.chunks[i]
            results.append({
                "id": chunk.id,
                "path": chunk.path,
                "text": chunk.text,
                "score": round(score, 3),
            })
        return results
