"""Адмінка бота на FastAPI. Повністю окремий модуль.

Що вміє:
- Дашборд: скільки питань, режими відповідей, оцінки, топ питань.
- Журнал питань і «Людина в циклі»: питання без відповіді або з 👎 —
  викладач пише відповідь, і бот віддає її наступного разу.
- Файли: завантаження HTML-уроків, перебудова індексу на сервері.
- Налаштування: кількість фрагментів, модель, ліміт, вимкнення бота.

Доступ: HTTP Basic (логін і пароль з ADMIN_USERNAME / ADMIN_PASSWORD).
Кожна форма містить CSRF-токен, похідний від пароля.

Модуль НЕ імпортується публічним ботом. Його підключає лише app.py
через диспетчер /admin. Якщо FastAPI не встановлено, бот працює далі.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import re
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from rag.runtime import EDITABLE_KEYS, Runtime

MAX_UPLOAD_BYTES = 3 * 1024 * 1024
FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}\.html$")

security = HTTPBasic(auto_error=False)


def build_admin(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="Адмінка бота", docs_url=None, redoc_url=None,
                  openapi_url=None)
    password = runtime.settings.admin_password
    username = runtime.settings.admin_username
    csrf_token = hmac.new(password.encode("utf-8"), b"csrf", hashlib.sha256).hexdigest() \
        if password else ""

    # --- доступ ------------------------------------------------------------

    def require_admin(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
        if not password:
            raise _plain(503, "ADMIN_PASSWORD не задано в .env — адмінка вимкнена")
        ok_user = credentials is not None and secrets.compare_digest(
            credentials.username.encode("utf-8"), username.encode("utf-8"))
        ok_pass = credentials is not None and secrets.compare_digest(
            credentials.password.encode("utf-8"), password.encode("utf-8"))
        if not (ok_user and ok_pass):
            raise _plain(401, "Потрібен вхід", {"WWW-Authenticate": 'Basic realm="admin"'})
        return credentials.username

    def check_csrf(token: str) -> None:
        if not token or not hmac.compare_digest(token, csrf_token):
            raise _plain(403, "Недійсна форма (CSRF). Оновіть сторінку і повторіть.")

    # --- оформлення ----------------------------------------------------------

    def layout(title: str, body: str, msg: str = "") -> HTMLResponse:
        nav = "".join(f'<a href="{href}">{label}</a>' for href, label in [
            ("/admin", "Дашборд"), ("/admin/questions", "Питання"),
            ("/admin/review", "Перевірка"), ("/admin/overrides", "Відповіді викладача"),
            ("/admin/files", "Файли"), ("/admin/settings", "Налаштування"),
        ])
        notice = f'<p class="msg">{html.escape(msg)}</p>' if msg else ""
        page = f"""<!doctype html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Адмінка · {html.escape(title)}</title>
<style>
body{{margin:0;font:15px/1.5 system-ui,sans-serif;background:#f4f6fa;color:#1a2233}}
header{{background:#1f2d47;color:#fff;padding:12px 18px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}}
header b{{margin-right:8px}}
header a{{color:#cfe0ff;text-decoration:none;padding:4px 6px;border-radius:6px}}
header a:hover{{background:#33466b}}
main{{max-width:1100px;margin:20px auto;padding:0 16px 60px}}
h1{{font-size:22px;margin:8px 0 14px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}}
.card{{background:#fff;border:1px solid #dde3ee;border-radius:10px;padding:14px}}
.card .num{{font-size:26px;font-weight:600}}
table{{width:100%;border-collapse:collapse;background:#fff;font-size:14px;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid #e6ebf3;padding:8px;text-align:left;vertical-align:top}}
th{{background:#eef2f9}}
input[type=text],input[type=number],select,textarea{{padding:8px;border:1px solid #c9d3e3;border-radius:6px;font:inherit;max-width:100%}}
textarea{{width:100%;min-height:120px}}
button,.btn{{padding:8px 14px;border:0;border-radius:6px;background:#2459be;color:#fff;cursor:pointer;font:inherit;text-decoration:none;display:inline-block}}
button.danger{{background:#b42323}}
.msg{{background:#e7f6ea;border:1px solid #a8dbb4;padding:10px;border-radius:8px}}
.muted{{color:#5d6b82;font-size:13px}}
pre{{white-space:pre-wrap;background:#0f1726;color:#e8f1ff;padding:10px;border-radius:8px;font-size:13px}}
form.inline{{display:inline}}
.row{{margin:8px 0}}
</style></head><body>
<header><b>Адмінка бота</b>{nav}</header>
<main><h1>{html.escape(title)}</h1>{notice}{body}</main>
</body></html>"""
        return HTMLResponse(page, headers={"X-Robots-Tag": "noindex, nofollow"})

    def csrf_field() -> str:
        return f'<input type="hidden" name="csrf" value="{csrf_token}">'

    def back(msg: str, to: str = "/admin") -> RedirectResponse:
        return RedirectResponse(f"{to}?msg={quote(msg)}", status_code=303)

    # --- дашборд -----------------------------------------------------------

    @app.get("/admin", response_class=HTMLResponse)
    def dashboard(request: Request, _user: str = Depends(require_admin)):
        s = runtime.store.stats(days=14)
        eff = runtime.effective()
        cards = "".join(
            f'<div class="card"><div class="muted">{label}</div><div class="num">{value}</div></div>'
            for label, value in [
                ("Усього питань", s["total"]),
                ("Корисно 👍", s["useful"]),
                ("Не допомогло 👎", s["not_useful"]),
                ("Чекають перевірки", s["pending_review"]),
                ("Середній час, мс", s["avg_latency_ms"]),
                ("Фрагментів у пошуку", runtime.chunk_count),
            ])
        modes = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>"
                        for k, v in sorted(s["by_mode"].items())) or "<tr><td colspan=2>Поки немає даних</td></tr>"
        days = "".join(f"<tr><td>{d}</td><td>{c}</td></tr>" for d, c in sorted(s["per_day"].items())) \
            or "<tr><td colspan=2>За останні 14 днів питань не було</td></tr>"
        top = "".join(f"<tr><td>{html.escape(t['question'])}</td><td>{t['count']}</td></tr>"
                      for t in s["top_questions"]) or "<tr><td colspan=2>—</td></tr>"
        body = f"""
<div class="cards">{cards}</div>
<p class="muted">Модель: {'увімкнено' if eff['use_llm'] else 'вимкнено'} ·
Бот: {'працює' if eff['public_enabled'] else 'ВИМКНЕНО'} ·
<a href="/admin/review">перейти до перевірки ({s['pending_review']})</a></p>
<h2 style="font-size:17px">Режими відповідей</h2>
<table><tr><th>Режим</th><th>Кількість</th></tr>{modes}</table>
<h2 style="font-size:17px">Питання за 14 днів</h2>
<table><tr><th>День (UTC)</th><th>Питань</th></tr>{days}</table>
<h2 style="font-size:17px">Найчастіші питання</h2>
<table><tr><th>Питання (нормалізоване)</th><th>Разів</th></tr>{top}</table>
"""
        return layout("Дашборд", body, request.query_params.get("msg", ""))

    # --- питання ------------------------------------------------------------

    def question_rows(items) -> str:
        rows = []
        for q in items:
            mark = {1: "👍", -1: "👎"}.get(q["feedback"], "")
            rows.append(
                f"<tr><td>{q['id']}</td><td>{html.escape(q['created_at'])}</td>"
                f"<td>{html.escape(q['question'])}</td><td>{q['mode']}</td>"
                f"<td>{mark}</td><td>{'так' if q['reviewed'] else 'ні'}</td>"
                f"<td><a href='/admin/questions/{q['id']}'>відкрити</a></td></tr>")
        return "".join(rows) or "<tr><td colspan=7>Порожньо</td></tr>"

    @app.get("/admin/questions", response_class=HTMLResponse)
    def questions(request: Request, _user: str = Depends(require_admin)):
        items = runtime.store.list_questions(limit=200)
        body = ("<table><tr><th>№</th><th>Час (UTC)</th><th>Питання</th><th>Режим</th>"
                "<th>Оцінка</th><th>Розглянуто</th><th></th></tr>"
                f"{question_rows(items)}</table>")
        return layout("Журнал питань", body, request.query_params.get("msg", ""))

    @app.get("/admin/review", response_class=HTMLResponse)
    def review(request: Request, _user: str = Depends(require_admin)):
        items = runtime.store.list_questions(limit=200, only_review=True)
        body = ("<p class='muted'>Тут питання без відповіді в конспекті або з 👎. "
                "Напишіть відповідь — бот віддаватиме її на це саме питання.</p>"
                "<table><tr><th>№</th><th>Час (UTC)</th><th>Питання</th><th>Режим</th>"
                "<th>Оцінка</th><th>Розглянуто</th><th></th></tr>"
                f"{question_rows(items)}</table>")
        return layout("Перевірка людиною", body, request.query_params.get("msg", ""))

    @app.get("/admin/questions/{question_id}", response_class=HTMLResponse)
    def question_detail(question_id: int, request: Request, _user: str = Depends(require_admin)):
        q = runtime.store.get_question(question_id)
        if not q:
            raise _plain(404, "Питання не знайдено")
        sources = q["source_ids"] or "[]"
        body = f"""
<p><b>Питання:</b> {html.escape(q['question'])}</p>
<p><b>Режим:</b> {q['mode']} · <b>Оцінка:</b> {q['feedback'] or '—'} ·
<b>Час:</b> {q['created_at']} UTC · <b>Затримка:</b> {q['latency_ms']} мс</p>
<p><b>Відповідь бота:</b></p><pre>{html.escape(q['answer'] or '')}</pre>
<p class="muted">Джерела (id фрагментів): {html.escape(sources)}
{('· помилка моделі: ' + html.escape(q['llm_error'])) if q['llm_error'] else ''}</p>
<h2 style="font-size:17px">Відповідь викладача</h2>
<form method="post" action="/admin/questions/{question_id}/answer">
{csrf_field()}
<textarea name="answer" required maxlength="4000" placeholder="Текст відповіді для студентів"></textarea>
<div class="row"><button type="submit">Зберегти відповідь</button></div>
</form>
<form method="post" action="/admin/questions/{question_id}/reviewed" class="inline">
{csrf_field()}<button type="submit" style="background:#5d6b82">Позначити розглянутим</button>
</form>
"""
        return layout(f"Питання №{question_id}", body, request.query_params.get("msg", ""))

    @app.post("/admin/questions/{question_id}/answer")
    def save_answer(question_id: int, answer: str = Form(...), csrf: str = Form(""),
                    _user: str = Depends(require_admin)):
        check_csrf(csrf)
        q = runtime.store.get_question(question_id)
        if not q:
            raise _plain(404, "Питання не знайдено")
        text = answer.strip()
        if not text or len(text) > 4000:
            return back("Відповідь порожня або задовга (до 4000 символів)",
                        f"/admin/questions/{question_id}")
        runtime.store.save_override(q["question"], text, from_question_id=question_id)
        runtime.store.mark_reviewed(question_id)
        return back("Відповідь збережено: бот віддаватиме її на це питання", "/admin/review")

    @app.post("/admin/questions/{question_id}/reviewed")
    def mark_reviewed(question_id: int, csrf: str = Form(""), _user: str = Depends(require_admin)):
        check_csrf(csrf)
        runtime.store.mark_reviewed(question_id)
        return back("Позначено розглянутим", "/admin/review")

    # --- відповіді викладача -----------------------------------------------

    @app.get("/admin/overrides", response_class=HTMLResponse)
    def overrides(request: Request, _user: str = Depends(require_admin)):
        rows = "".join(
            f"<tr><td>{html.escape(o['question'])}</td><td>{html.escape(o['answer'])}</td>"
            f"<td>{o['updated_at']}</td><td>"
            f"<form method='post' action='/admin/overrides/delete' class='inline'>"
            f"{csrf_field()}<input type='hidden' name='norm' value='{html.escape(o['norm'])}'>"
            f"<button class='danger' type='submit'>видалити</button></form></td></tr>"
            for o in runtime.store.list_overrides())
        body = ("<table><tr><th>Питання</th><th>Відповідь</th><th>Оновлено (UTC)</th><th></th></tr>"
                f"{rows or '<tr><td colspan=4>Ще немає відповідей викладача</td></tr>'}</table>")
        return layout("Відповіді викладача", body, request.query_params.get("msg", ""))

    @app.post("/admin/overrides/delete")
    def delete_override(norm: str = Form(...), csrf: str = Form(""),
                        _user: str = Depends(require_admin)):
        check_csrf(csrf)
        runtime.store.delete_override(norm)
        return back("Відповідь видалено", "/admin/overrides")

    # --- файли і індекс ----------------------------------------------------

    @app.get("/admin/files", response_class=HTMLResponse)
    def files(request: Request, _user: str = Depends(require_admin)):
        repo = runtime.settings.repo_source_dir
        repo_names = {p.name for p in repo.glob("*.html")} if repo.is_dir() else set()
        uploaded = {p.name for p in runtime.sources_dir.glob("*.html")} \
            if runtime.sources_dir.is_dir() else set()
        rows = []
        for name in sorted(set(repo_names) | uploaded):
            origin = "завантажено адміном" if name in uploaded else "з репозиторію"
            action = ""
            if name in uploaded:
                action = (f"<form method='post' action='/admin/files/delete' class='inline'>"
                          f"{csrf_field()}<input type='hidden' name='name' value='{html.escape(name)}'>"
                          f"<button class='danger' type='submit'>видалити</button></form>")
            rows.append(f"<tr><td>{html.escape(name)}</td><td>{origin}</td><td>{action}</td></tr>")
        body = f"""
<h2 style="font-size:17px">Уроки в базі знань</h2>
<table><tr><th>Файл</th><th>Походження</th><th></th></tr>{''.join(rows)}</table>
<h2 style="font-size:17px">Завантажити HTML-урок</h2>
<p class="muted">Файл має бути в UTF-8, до 3 МБ, назва латиницею (наприклад lesson_47.html).
Після завантаження натисніть «Перебудувати індекс», щоб бот почав користуватися новим матеріалом.</p>
<form method="post" action="/admin/files/upload" enctype="multipart/form-data">
{csrf_field()}
<input type="file" name="file" accept=".html,text/html" required>
<button type="submit">Завантажити</button>
</form>
<h2 style="font-size:17px">Індекс</h2>
<p>Фрагментів зараз: <b>{runtime.chunk_count}</b></p>
<form method="post" action="/admin/index/rebuild">{csrf_field()}
<button type="submit">Перебудувати індекс</button></form>
"""
        return layout("Файли і індекс", body, request.query_params.get("msg", ""))

    @app.post("/admin/files/upload")
    async def upload(file: UploadFile, csrf: str = Form(""), _user: str = Depends(require_admin)):
        check_csrf(csrf)
        name = (file.filename or "").strip()
        if not FILENAME_RE.match(name):
            return back("Назва файлу має бути латиницею, наприклад lesson_47.html", "/admin/files")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return back("Файл більший за 3 МБ", "/admin/files")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return back("Файл має бути в кодуванні UTF-8", "/admin/files")
        runtime.sources_dir.mkdir(parents=True, exist_ok=True)
        (runtime.sources_dir / name).write_bytes(content)
        return back(f"Файл {name} збережено. Натисніть «Перебудувати індекс».", "/admin/files")

    @app.post("/admin/files/delete")
    def delete_file(name: str = Form(...), csrf: str = Form(""), _user: str = Depends(require_admin)):
        check_csrf(csrf)
        if not FILENAME_RE.match(name):
            return back("Некоректна назва", "/admin/files")
        target = runtime.sources_dir / name
        if target.is_file():
            target.unlink()
            return back(f"Файл {name} видалено. Перебудуйте індекс.", "/admin/files")
        return back("Такого завантаженого файлу немає (репозиторні уроки не видаляються)", "/admin/files")

    @app.post("/admin/index/rebuild")
    def rebuild(csrf: str = Form(""), _user: str = Depends(require_admin)):
        check_csrf(csrf)
        try:
            count = runtime.rebuild_index()
        except (ValueError, OSError) as exc:
            return back(f"Не вдалося перебудувати індекс: {exc}", "/admin/files")
        return back(f"Індекс перебудовано: {count} фрагментів", "/admin/files")

    # --- налаштування -------------------------------------------------------

    @app.get("/admin/settings", response_class=HTMLResponse)
    def settings_page(request: Request, _user: str = Depends(require_admin)):
        eff = runtime.effective()
        body = f"""
<form method="post" action="/admin/settings">
{csrf_field()}
<div class="row"><label>Фрагментів у відповіді (1–10):
<input type="number" name="top_k" min="1" max="10" value="{eff['top_k']}"></label></div>
<div class="row"><label><input type="checkbox" name="use_llm" value="1" {'checked' if eff['use_llm'] else ''}>
Використовувати модель OpenAI</label>
<span class="muted">{'ключ є' if eff['llm_available'] else 'ключа OpenAI немає в .env — модель не працюватиме'}</span></div>
<div class="row"><label>Модель:
<input type="text" name="openai_model" maxlength="60" value="{html.escape(str(eff['openai_model']))}"></label></div>
<div class="row"><label>Запитів до моделі за хвилину (1–1000):
<input type="number" name="llm_per_minute" min="1" max="1000" value="{eff['llm_per_minute']}"></label></div>
<div class="row"><label><input type="checkbox" name="public_enabled" value="1" {'checked' if eff['public_enabled'] else ''}>
Бот доступний студентам</label></div>
<div class="row"><button type="submit">Зберегти</button></div>
</form>
<p class="muted">Ключ OpenAI адмінка не показує і не змінює: його задає .env (див. docs/ADMIN.md).</p>
"""
        return layout("Налаштування", body, request.query_params.get("msg", ""))

    @app.post("/admin/settings")
    def save_settings(top_k: int = Form(4), openai_model: str = Form("gpt-4.1-mini"),
                      llm_per_minute: int = Form(20), use_llm: str = Form(""),
                      public_enabled: str = Form(""), csrf: str = Form(""),
                      _user: str = Depends(require_admin)):
        check_csrf(csrf)
        model = openai_model.strip()
        if not re.match(r"^[A-Za-z0-9._\-]{1,60}$", model):
            return back("Некоректна назва моделі", "/admin/settings")
        values = {
            "top_k": min(max(int(top_k), 1), 10),
            "use_llm": bool(use_llm),
            "openai_model": model,
            "llm_per_minute": min(max(int(llm_per_minute), 1), 1000),
            "public_enabled": bool(public_enabled),
        }
        assert set(values) == set(EDITABLE_KEYS)
        for key, value in values.items():
            runtime.store.set_setting(key, value)
        return back("Налаштування збережено", "/admin/settings")

    return app


def _plain(status: int, message: str, headers: Optional[dict] = None):
    """Короткі відповіді помилок без HTML-оболонки."""
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail=message, headers=headers)
