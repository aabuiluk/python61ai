"""Необов'язкова генерація відповіді через OpenAI (лише стандартна бібліотека).

RAG працює і без цього модуля: тоді користувач бачить знайдені фрагменти
уроку. Якщо OPENAI_API_KEY задано і запит до OpenAI вдався, відповідь
пише модель, спираючись ЛИШЕ на знайдені фрагменти.

Важливо:
- ключ ніколи не виводимо в відповідь і в логи;
- при будь-якій помилці піднімаємо LLMError з коротким повідомленням,
  а web-шар повертає фрагменти без відповіді моделі;
- таймаут короткий: безплатний PythonAnywhere обмежує час процесу.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Dict, List

from rag.netutil import ssl_context

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_SECONDS = 20


class LLMError(RuntimeError):
    """Помилка виклику моделі. Повідомлення безпечне, без ключа."""


SYSTEM_PROMPT = (
    "Ви асистент курсу Python/AI. Відповідайте українською, коротко і по суті. "
    "Використовуйте ЛИШЕ наведені уривки уроку. Якщо відповіді в уривках немає, "
    "прямо скажіть про це і не вигадуйте. Посилайтеся на номери уривків у "
    "квадратних дужках, наприклад [1]."
)


def build_messages(question: str, passages: List[Dict[str, object]]) -> List[dict]:
    """Формує повідомлення для chat/completions."""
    context_lines = []
    for number, passage in enumerate(passages, start=1):
        context_lines.append(f"[{number}] ({passage['path']})\n{passage['text']}")
    user_content = "Уривки уроку:\n\n" + "\n\n".join(context_lines)
    user_content += f"\n\nПитання: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def ask_openai(question: str, passages: List[Dict[str, object]], api_key: str,
               model: str) -> str:
    """Відправляє запит до OpenAI і повертає текст відповіді."""
    body = json.dumps({
        "model": model,
        "messages": build_messages(question, passages),
        "max_tokens": 500,
        "temperature": 0.2,
    }).encode("utf-8")

    request = urllib.request.Request(
        OPENAI_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS,
                                   context=ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Тіло помилки не віддаємо назовні: там можуть бути деталі облікового запису.
        raise LLMError(f"OpenAI повернув HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LLMError("Немає доступу до OpenAI (мережа або таймаут)") from None
    except json.JSONDecodeError:
        raise LLMError("Некоректна відповідь OpenAI") from None

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("У відповіді OpenAI немає тексту") from None
    text = (text or "").strip()
    if not text:
        raise LLMError("OpenAI повернув порожню відповідь")
    return text
