"""Тести адмінки (FastAPI). Потрібні залежності з requirements-dev.txt.

Запуск:  python -m unittest discover -s tests -t . -v
Без FastAPI тести пропускаються (публічний бот від цього не залежить).
"""

from __future__ import annotations

import base64
import io
import re
import tempfile
import unittest
from pathlib import Path

from rag.config import PROJECT_DIR
from tests.test_rag import make_settings

try:
    from fastapi.testclient import TestClient

    from admin.app import build_admin
    from rag.runtime import Runtime
    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover — залежить від середовища
    HAVE_FASTAPI = False

PASSWORD = "test-pass-123"
LESSON = PROJECT_DIR / "data" / "source" / "PythonAI_Author_Lesson45_Lesson46.html"


def basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@unittest.skipUnless(HAVE_FASTAPI, "немає FastAPI (встановіть requirements-dev.txt)")
class AdminTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(make_settings(Path(self._tmp.name), admin_password=PASSWORD))
        self.client = TestClient(build_admin(self.runtime))
        self.auth = basic("admin", PASSWORD)

    def tearDown(self):
        self._tmp.cleanup()

    def csrf(self) -> str:
        page = self.client.get("/admin/settings", headers=self.auth).text
        return re.search(r'name="csrf" value="([0-9a-f]+)"', page).group(1)

    def test_no_password_configured_disables_admin(self):
        runtime = Runtime(make_settings(Path(self._tmp.name) / "x", admin_password=""))
        client = TestClient(build_admin(runtime))
        self.assertEqual(client.get("/admin").status_code, 503)

    def test_requires_basic_auth(self):
        self.assertEqual(self.client.get("/admin").status_code, 401)
        self.assertEqual(self.client.get("/admin", headers=basic("admin", "wrong")).status_code, 401)
        self.assertEqual(self.client.get("/admin", headers=basic("root", PASSWORD)).status_code, 401)

    def test_dashboard_with_auth(self):
        response = self.client.get("/admin", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Дашборд", response.text)
        self.assertEqual(response.headers.get("x-robots-tag"), "noindex, nofollow")

    def test_post_without_csrf_is_forbidden(self):
        qid = self.runtime.store.log_question("питання", "no_match", "", [], None, 1)
        response = self.client.post(f"/admin/questions/{qid}/answer",
                                    data={"answer": "відповідь"}, headers=self.auth)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.runtime.store.find_override("питання"))

    def test_human_answer_creates_override(self):
        qid = self.runtime.store.log_question("Рецепт борщу", "no_match", "", [], None, 1)
        response = self.client.post(f"/admin/questions/{qid}/answer",
                                    data={"answer": "Це не тема уроку.", "csrf": self.csrf()},
                                    headers=self.auth, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.runtime.store.find_override("рецепт борщу")["answer"],
                         "Це не тема уроку.")
        self.assertEqual(self.runtime.store.get_question(qid)["reviewed"], 1)

    def test_settings_are_saved(self):
        response = self.client.post("/admin/settings", data={
            "top_k": "6", "openai_model": "gpt-4.1-mini", "llm_per_minute": "15",
            "public_enabled": "", "csrf": self.csrf(),
        }, headers=self.auth, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        eff = self.runtime.effective()
        self.assertEqual(eff["top_k"], 6)
        self.assertEqual(eff["llm_per_minute"], 15)
        self.assertFalse(eff["public_enabled"])

    def test_upload_and_rebuild_index(self):
        before = self.runtime.chunk_count
        html = "<h1>Новий урок</h1><p>Тестовий матеріал про дедлайн T900.</p>".encode("utf-8")
        response = self.client.post(
            "/admin/files/upload?name=lesson_99.html", headers={
                **self.auth, "X-CSRF": self.csrf(), "Content-Type": "application/octet-stream"},
            content=html, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue((self.runtime.sources_dir / "lesson_99.html").is_file())
        count = self.client.post("/admin/index/rebuild", headers=self.auth,
                                 data={"csrf": self.csrf()}, follow_redirects=False)
        self.assertEqual(count.status_code, 303)
        self.assertGreater(self.runtime.chunk_count, before)
        self.assertTrue(self.runtime.index().search("дедлайн T900"))

    def test_upload_rejects_bad_name(self):
        response = self.client.post(
            "/admin/files/upload?name=../evil.html", headers={
                **self.auth, "X-CSRF": self.csrf(), "Content-Type": "application/octet-stream"},
            content=b"<p>x</p>", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertFalse((self.runtime.sources_dir.parent / "evil.html").exists())
        self.assertFalse(self.runtime.sources_dir.exists() and any(self.runtime.sources_dir.iterdir()))

    def test_wsgi_bridge_serves_admin_without_threads(self):
        from rag.asgi_bridge import asgi_to_wsgi
        wsgi = asgi_to_wsgi(build_admin(self.runtime))
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/admin", "QUERY_STRING": "",
                   "CONTENT_LENGTH": "", "wsgi.input": io.BytesIO(b""),
                   "wsgi.url_scheme": "https", "SERVER_NAME": "t", "SERVER_PORT": "443",
                   "HTTP_AUTHORIZATION": self.auth["Authorization"]}
        body = b"".join(wsgi(environ, start_response))
        self.assertTrue(captured["status"].startswith("200"))
        self.assertIn("Дашборд".encode("utf-8"), body)

    def test_repo_lesson_is_listed(self):
        page = self.client.get("/admin/files", headers=self.auth).text
        self.assertIn(LESSON.name, page)


if __name__ == "__main__":
    unittest.main()
