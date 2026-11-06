"""WSGI-застосунок: сторінка з полем питання і JSON API.

Маршрути:
- GET  /            — проста сторінка для питань (HTML);
- GET  /api/health  — перевірка, що сервер живий і індекс завантажено;
- GET  /api/search?q=...  — лише пошук фрагментів (без моделі);
- POST /api/ask     — питання: JSON {"question": "..."} -> відповідь + джерела.

Жодних сторонніх бібліотек: застосунок запускається на PythonAnywhere
без virtualenv і без pip install.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Callable, Dict, List
from urllib.parse import parse_qs

from rag.config import Settings, load_settings
from rag.llm import LLMError, ask_openai
from rag.search import BM25Index
from rag.text import Chunk

MAX_QUESTION_CHARS = 300
MAX_BODY_BYTES = 4096
# Захист квоти: не більше стількох запитів до OpenAI за хвилину на весь сервер.
LLM_CALLS_PER_MINUTE = 20

NO_MATCH_ANSWER = (
    "У матеріалах уроку не знайдено відповіді на це питання. "
    "Спробуйте сформулювати його інакше, використавши терміни з уроку."
)


class _RateLimiter:
    """Просте обмеження кількості викликів за ковзні 60 секунд."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= self._limit:
                return False
            self._calls.append(now)
            return True


def load_chunks(path) -> List[Chunk]:
    """Читає data/chunks.json у список Chunk."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return [Chunk(id=item["id"], path=item["path"], text=item["text"])
            for item in payload["chunks"]]


def answer_question(question: str, index: BM25Index, settings: Settings,
                    limiter: _RateLimiter, top_k: int) -> Dict[str, object]:
    """Головна логіка RAG: пошук -> (за наявності ключа) відповідь моделі."""
    passages = index.search(question, top_k=top_k)
    sources = [{"id": p["id"], "path": p["path"], "text": p["text"],
                "score": p["score"]} for p in passages]

    if not passages:
        return {"answer": NO_MATCH_ANSWER, "mode": "no_match", "sources": []}

    result: Dict[str, object] = {"sources": sources}
    if settings.use_llm and limiter.allow():
        try:
            result["answer"] = ask_openai(question, passages,
                                          settings.openai_api_key,
                                          settings.openai_model)
            result["mode"] = "llm"
            return result
        except LLMError as exc:
            # Не ламаємо відповідь: показуємо джерела і пояснюємо, що сталось.
            result["llm_error"] = str(exc)
    elif settings.use_llm:
        result["llm_error"] = "Перевищено ліміт запитів до моделі за хвилину"

    result["answer"] = ("Модель недоступна, тому показуємо найбільш релевантні "
                        "фрагменти уроку. Перегляньте джерела нижче.")
    result["mode"] = "retrieval"
    return result


PAGE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Питання до уроків 45–46</title>
<style>
  :root { --bg:#f5f7fb; --fg:#182438; --card:#ffffff; --line:#dbe3ee; --accent:#2459be; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#101722; --fg:#e8edf7; --card:#1b2635; --line:#526277; --accent:#93b7ff; }
  }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:17px/1.6 system-ui, sans-serif; }
  main { max-width:860px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:26px; margin:12px 0 4px; }
  .muted { opacity:.75; font-size:15px; }
  form { display:flex; gap:8px; margin:20px 0; flex-wrap:wrap; }
  input[type=text] { flex:1 1 260px; padding:12px; font-size:16px; border-radius:8px;
                     border:1px solid var(--line); background:var(--card); color:var(--fg); }
  button { padding:12px 18px; font-size:16px; border-radius:8px; border:0;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.5; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:16px; margin:12px 0; }
  .answer { white-space:pre-wrap; }
  .source { font-size:14px; }
  .source pre { white-space:pre-wrap; background:transparent; margin:6px 0 0;
                font-family:inherit; font-size:14px; line-height:1.5; }
  .badge { font-size:13px; color:var(--accent); }
</style>
</head>
<body>
<main>
  <h1>Питання до уроків 45–46</h1>
  <p class="muted">Відповіді береться з конспекту пари: спершу знаходимо релевантні
     фрагменти, далі (якщо доступна модель) формуємо відповідь лише з них.</p>
  <form id="ask-form">
    <input type="text" id="question" maxlength="300" required
           placeholder="Наприклад: Який SLA у заявки T101?">
    <button type="submit" id="send">Запитати</button>
  </form>
  <div id="result"></div>
</main>
<script>
const form = document.getElementById("ask-form");
const result = document.getElementById("result");
const button = document.getElementById("send");

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  node.textContent = text;   // textContent: нічого з відповіді не виконується як HTML
  return node;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = document.getElementById("question").value.trim();
  if (!question) return;
  button.disabled = true;
  result.replaceChildren(el("p", "Шукаю в уроці…", "muted"));
  try {
    const response = await fetch("api/ask", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Помилка сервера");
    result.replaceChildren();
    const badge = el("p", data.mode === "llm" ? "Відповідь сформована моделлю"
      : data.mode === "retrieval" ? "Режим без моделі: лише джерела" : "", "badge");
    const card = el("div", data.answer, "card answer");
    result.append(badge, card);
    if (data.llm_error) result.append(el("p", "Модель: " + data.llm_error, "muted"));
    for (const source of data.sources || []) {
      const box = el("div", "", "card source");
      box.append(el("strong", source.path + "  (релевантність " + source.score + ")"));
      box.append(el("pre", source.text));
      result.append(box);
    }
  } catch (error) {
    result.replaceChildren(el("p", "Не вдалося отримати відповідь: " + error.message));
  } finally {
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""


def create_app(settings: Settings | None = None) -> Callable:
    """Створює WSGI-застосунок. Викликається один раз при старті процесу."""
    settings = settings or load_settings()
    chunks = load_chunks(settings.chunks_path)
    index = BM25Index(chunks)
    limiter = _RateLimiter(LLM_CALLS_PER_MINUTE)

    def respond(start_response, status: str, body: bytes,
                content_type: str = "application/json; charset=utf-8"):
        start_response(status, [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
        ])
        return [body]

    def json_response(start_response, status: str, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return respond(start_response, status, body)

    def application(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"

        if method == "GET" and path == "/":
            return respond(start_response, "200 OK", PAGE.encode("utf-8"),
                           "text/html; charset=utf-8")

        if method == "GET" and path == "/api/health":
            return json_response(start_response, "200 OK", {
                "ok": True,
                "chunks": len(chunks),
                "llm_enabled": settings.use_llm,
                "model": settings.openai_model if settings.use_llm else None,
            })

        if method == "GET" and path == "/api/search":
            query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0]
            query = query.strip()[:MAX_QUESTION_CHARS]
            if not query:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Порожній параметр q"})
            results = index.search(query, top_k=settings.top_k)
            return json_response(start_response, "200 OK",
                                 {"query": query, "results": results})

        if method == "POST" and path == "/api/ask":
            try:
                length = int(environ.get("CONTENT_LENGTH") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Очікую JSON з полем question"})
            try:
                data = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
                question = str(data.get("question", "")).strip()
            except (ValueError, AttributeError, UnicodeDecodeError):
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Некоректний JSON"})
            if not question:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Порожнє питання"})
            if len(question) > MAX_QUESTION_CHARS:
                return json_response(start_response, "400 Bad Request",
                                     {"error": f"Питання довше {MAX_QUESTION_CHARS} символів"})
            answer = answer_question(question, index, settings, limiter,
                                     settings.top_k)
            return json_response(start_response, "200 OK", answer)

        if path.startswith("/api/"):
            return json_response(start_response, "404 Not Found", {"error": "Немає такого маршруту"})
        return respond(start_response, "404 Not Found", b"Not found", "text/plain; charset=utf-8")

    return application
