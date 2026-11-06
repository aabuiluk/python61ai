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
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs

from rag.llm import LLMError, ask_openai
from rag.runtime import Runtime, get_runtime

MAX_QUESTION_CHARS = 300
MAX_BODY_BYTES = 4096

NO_MATCH_ANSWER = (
    "У матеріалах уроку не знайдено відповіді на це питання. "
    "Спробуйте сформулювати його інакше, використавши терміни з уроку. "
    "Ваше питання передано викладачеві, і відповідь з'явиться тут пізніше."
)
DISABLED_MESSAGE = "Бот тимчасово вимкнено адміністратором. Спробуйте пізніше."


class _RateLimiter:
    """Обмеження кількості викликів моделі за ковзні 60 секунд."""

    def __init__(self) -> None:
        self._calls: deque = deque()
        self._lock = threading.Lock()

    def allow(self, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= limit:
                return False
            self._calls.append(now)
            return True


def answer_question(question: str, runtime: Runtime, limiter: _RateLimiter) -> Dict[str, object]:
    """RAG-логіка: відповідь викладача -> пошук -> (за наявності ключа) модель."""
    started = time.monotonic()
    eff = runtime.effective()
    store = runtime.store

    override = store.find_override(question)
    if override:
        result: Dict[str, object] = {"answer": override["answer"], "mode": "human",
                                     "sources": []}
        llm_error = None
    else:
        passages = runtime.index().search(question, top_k=int(eff["top_k"]))
        sources = [{"id": p["id"], "path": p["path"], "text": p["text"],
                    "score": p["score"]} for p in passages]
        result = {"sources": sources}
        llm_error = None
        if not passages:
            result.update(answer=NO_MATCH_ANSWER, mode="no_match")
        else:
            answered = False
            if eff["use_llm"]:
                if limiter.allow(int(eff["llm_per_minute"])):
                    try:
                        result["answer"] = ask_openai(
                            question, passages, runtime.settings.openai_api_key,
                            str(eff["openai_model"]))
                        result["mode"] = "llm"
                        answered = True
                    except LLMError as exc:
                        llm_error = str(exc)
                else:
                    llm_error = "Перевищено ліміт запитів до моделі за хвилину"
            if not answered:
                result.update(answer=("Модель недоступна, тому показуємо найбільш "
                                      "релевантні фрагменти уроку. Перегляньте джерела нижче."),
                              mode="retrieval")

    if llm_error:
        result["llm_error"] = llm_error

    # Журнал не має ламати відповідь: якщо запис не вдався, бот однаково відповість.
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        result["question_id"] = store.log_question(
            question, str(result["mode"]), str(result["answer"]),
            [s["id"] for s in result["sources"]], llm_error, latency_ms)
    except Exception:  # noqa: BLE001 — журнал другорядний
        result["question_id"] = None
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
  .feedback button { background:transparent; color:var(--fg); border:1px solid var(--line); padding:6px 12px; font-size:14px; margin-right:6px; }
  .feedback button.on { border-color:var(--accent); color:var(--accent); }
</style>
</head>
<body>
<main>
  <h1>Питання до уроків 45–46</h1>
  <p class="muted">Відповідь береться з конспекту пари: спершу знаходимо релевантні
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
const LABELS = {
  llm: "Відповідь сформована моделлю",
  retrieval: "Режим без моделі: лише джерела",
  human: "Відповідь викладача",
  no_match: "У конспекті немає відповіді"
};

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  node.textContent = text;   // textContent: нічого з відповіді не виконується як HTML
  return node;
}

function feedbackRow(questionId) {
  const row = el("div", "", "feedback");
  row.append(el("span", "Чи була відповідь корисною? ", "muted"));
  const yes = el("button", "👍 Так");
  const no = el("button", "👎 Ні");
  const send = async (value, node) => {
    if (!questionId) return;
    yes.disabled = no.disabled = true;
    node.classList.add("on");
    try {
      await fetch("api/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question_id: questionId, value})
      });
      row.append(el("span", "Дякуємо!", "muted"));
    } catch (e) {
      row.append(el("span", "Не вдалося надіслати оцінку", "muted"));
    }
  };
  yes.addEventListener("click", () => send(1, yes));
  no.addEventListener("click", () => send(-1, no));
  row.append(yes, no);
  return row;
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
    result.append(el("p", LABELS[data.mode] || "", "badge"));
    result.append(el("div", data.answer, "card answer"));
    if (data.llm_error) result.append(el("p", "Модель: " + data.llm_error, "muted"));
    for (const source of data.sources || []) {
      const box = el("div", "", "card source");
      box.append(el("strong", source.path + "  (релевантність " + source.score + ")"));
      box.append(el("pre", source.text));
      result.append(box);
    }
    result.append(feedbackRow(data.question_id));
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


def create_app(runtime: Optional[Runtime] = None) -> Callable:
    """Створює WSGI-застосунок бота. Викликається один раз при старті процесу."""
    runtime = runtime or get_runtime()
    limiter = _RateLimiter()

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

    def read_json(environ) -> Optional[dict]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            return None
        try:
            data = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def application(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"

        if method == "GET" and path == "/":
            return respond(start_response, "200 OK", PAGE.encode("utf-8"),
                           "text/html; charset=utf-8")

        if method == "GET" and path == "/api/health":
            eff = runtime.effective()
            return json_response(start_response, "200 OK", {
                "ok": True,
                "chunks": runtime.chunk_count,
                "llm_enabled": bool(eff["use_llm"]),
                "model": eff["openai_model"] if eff["use_llm"] else None,
                "public_enabled": eff["public_enabled"],
            })

        if method == "GET" and path == "/api/search":
            query = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0]
            query = query.strip()[:MAX_QUESTION_CHARS]
            if not query:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Порожній параметр q"})
            results = runtime.index().search(query, top_k=int(runtime.effective()["top_k"]))
            return json_response(start_response, "200 OK",
                                 {"query": query, "results": results})

        if method == "POST" and path == "/api/ask":
            if not runtime.effective()["public_enabled"]:
                return json_response(start_response, "503 Service Unavailable",
                                     {"error": DISABLED_MESSAGE})
            data = read_json(environ)
            if data is None:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Очікую JSON з полем question"})
            question = str(data.get("question", "")).strip()
            if not question:
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Порожнє питання"})
            if len(question) > MAX_QUESTION_CHARS:
                return json_response(start_response, "400 Bad Request",
                                     {"error": f"Питання довше {MAX_QUESTION_CHARS} символів"})
            answer = answer_question(question, runtime, limiter)
            return json_response(start_response, "200 OK", answer)

        if method == "POST" and path == "/api/feedback":
            data = read_json(environ)
            try:
                question_id = int(data["question_id"]) if data else 0
                value = int(data["value"]) if data else 0
            except (KeyError, TypeError, ValueError):
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Очікую question_id і value (1 або -1)"})
            if value not in (1, -1) or not runtime.store.set_feedback(question_id, value):
                return json_response(start_response, "400 Bad Request",
                                     {"error": "Невідоме питання або некоректна оцінка"})
            return json_response(start_response, "200 OK", {"ok": True})

        if path.startswith("/api/"):
            return json_response(start_response, "404 Not Found",
                                 {"error": "Немає такого маршруту"})
        return respond(start_response, "404 Not Found", b"Not found",
                       "text/plain; charset=utf-8")

    return application
