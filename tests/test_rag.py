"""Тести публічного бота (стандартна бібліотека, без мережі).

Запуск з кореня проєкту:
    python -m unittest discover -s tests -t . -v

Ключ OpenAI в тестах завжди порожній, тож бот працює в режимі «лише джерела».
Тести адмінки — в tests/test_admin.py (потребують requirements-dev.txt).
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from rag.build import build_chunks
from rag.config import PROJECT_DIR, Settings, load_env_file
from rag.runtime import Runtime, load_chunks
from rag.search import BM25Index, tokenize
from rag.store import normalize_question
from rag.text import parse_lesson_html
from rag.web import create_app

LESSON = PROJECT_DIR / "data" / "source" / "PythonAI_Author_Lesson45_Lesson46.html"
CHUNKS = PROJECT_DIR / "data" / "chunks.json"


def make_settings(data_dir: Path, api_key: str = "", admin_password: str = "") -> Settings:
    return Settings(
        repo_chunks_path=CHUNKS,
        repo_source_dir=PROJECT_DIR / "data" / "source",
        data_dir=data_dir,
        openai_api_key=api_key,
        openai_model="test-model",
        use_llm=bool(api_key),
        top_k=4,
        llm_per_minute=20,
        admin_username="admin",
        admin_password=admin_password,
    )


class ParserTests(unittest.TestCase):
    def test_lesson_parses_into_chunks(self):
        chunks = parse_lesson_html(LESSON.read_text(encoding="utf-8"), "lesson")
        self.assertGreater(len(chunks), 20)
        joined = "\n".join(c.text for c in chunks)
        self.assertNotIn("Зміст практикуму", joined)
        self.assertNotIn("Пари 45–46 · Студент", joined)
        self.assertNotIn("<p>", joined)

    def test_headings_build_path(self):
        chunks = parse_lesson_html("<h1>Тема</h1><p>Вступний абзац.</p>"
                                   "<h2>Розділ</h2><p>Текст розділу.</p>", "x")
        paths = {c.text: c.path for c in chunks}
        self.assertEqual(paths["Текст розділу."], "Тема > Розділ")

    def test_code_blocks_keep_newlines(self):
        chunks = parse_lesson_html("<pre><code>a = 1\nb = 2</code></pre>", "x")
        self.assertIn("a = 1\nb = 2", chunks[0].text)


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = BM25Index(load_chunks(CHUNKS))

    def test_tokenize_stems_long_words(self):
        self.assertEqual(tokenize("заявки"), tokenize("заявка"))
        self.assertIn("t101", tokenize("T101"))

    def test_sla_question_finds_sla_fragment(self):
        results = self.index.search("Який SLA у заявки T101?", top_k=3)
        self.assertIn("T101", " ".join(r["text"] for r in results))

    def test_unrelated_question_has_no_results(self):
        self.assertEqual(self.index.search("погода в Києві завтра сонячна"), [])


class BuildTests(unittest.TestCase):
    def test_build_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "chunks.json"
            count = build_chunks([str(LESSON)], out)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["chunk_count"], count)
            self.assertEqual(payload["source_files"], [LESSON.name])


class ConfigTests(unittest.TestCase):
    def test_env_file_does_not_override_existing_variables(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"RAG_TEST_B": "from-env"}, clear=False):
            env_path = Path(tmp) / ".env"
            env_path.write_text('RAG_TEST_A="from-file"\nRAG_TEST_B=file\n# коментар\n',
                                encoding="utf-8")
            load_env_file(env_path)
            self.assertEqual(os.environ["RAG_TEST_A"], "from-file")
            self.assertEqual(os.environ["RAG_TEST_B"], "from-env")
            os.environ.pop("RAG_TEST_A", None)

    def test_normalize_question(self):
        self.assertEqual(normalize_question("  Який SLA, у T101?! "), "який sla у t101")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(make_settings(Path(self._tmp.name)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_log_feedback_and_stats(self):
        store = self.runtime.store
        qid = store.log_question("Питання?", "retrieval", "ans", ["c1"], None, 12)
        self.assertTrue(store.set_feedback(qid, -1))
        stats = store.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["not_useful"], 1)
        self.assertEqual(stats["pending_review"], 1)

    def test_override_matches_normalized_question(self):
        store = self.runtime.store
        store.save_override("Який SLA у T101?", "Чотири години")
        self.assertEqual(store.find_override("який sla у t101")["answer"], "Чотири години")
        self.assertTrue(store.delete_override(normalize_question("Який SLA у T101?")))

    def test_settings_override_env_defaults(self):
        self.runtime.store.set_setting("top_k", 7)
        self.assertEqual(self.runtime.effective()["top_k"], 7)
        self.assertFalse(self.runtime.effective()["use_llm"])  # ключа немає


class WebTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(make_settings(Path(self._tmp.name)))
        self.app = create_app(self.runtime)

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self, method, path, body=b"", query=""):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
        raw = b"".join(self.app(environ, start_response))
        return captured["status"], raw

    def test_home_page_served(self):
        status, body = self._call("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("Питання до уроків".encode("utf-8"), body)

    def test_health(self):
        status, body = self._call("GET", "/api/health")
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertGreater(data["chunks"], 20)
        self.assertFalse(data["llm_enabled"])

    def test_ask_returns_sources_and_question_id(self):
        payload = json.dumps({"question": "Який SLA у заявки T101?"}).encode("utf-8")
        status, body = self._call("POST", "/api/ask", payload)
        data = json.loads(body)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["mode"], "retrieval")
        self.assertTrue(data["sources"])
        self.assertIsNotNone(data["question_id"])

    def test_feedback_is_stored(self):
        payload = json.dumps({"question": "Який SLA у заявки T101?"}).encode("utf-8")
        _, body = self._call("POST", "/api/ask", payload)
        qid = json.loads(body)["question_id"]
        status, _ = self._call("POST", "/api/feedback",
                               json.dumps({"question_id": qid, "value": -1}).encode())
        self.assertTrue(status.startswith("200"))
        self.assertEqual(self.runtime.store.get_question(qid)["feedback"], -1)

    def test_human_override_is_served(self):
        self.runtime.store.save_override("Рецепт борщу", "Такого в уроці немає — звертайтесь до викладача.")
        payload = json.dumps({"question": "рецепт борщу"}).encode("utf-8")
        _, body = self._call("POST", "/api/ask", payload)
        data = json.loads(body)
        self.assertEqual(data["mode"], "human")
        self.assertIn("викладача", data["answer"])

    def test_disabled_bot_returns_503(self):
        self.runtime.store.set_setting("public_enabled", False)
        status, _ = self._call("POST", "/api/ask", b'{"question": "T101"}')
        self.assertTrue(status.startswith("503"))

    def test_ask_rejects_bad_input(self):
        self.assertTrue(self._call("POST", "/api/ask", b'{"question": "   "}')[0].startswith("400"))
        long_q = json.dumps({"question": "а" * 301}).encode("utf-8")
        self.assertTrue(self._call("POST", "/api/ask", long_q)[0].startswith("400"))
        self.assertTrue(self._call("POST", "/api/ask", b"not json")[0].startswith("400"))

    def test_search_and_unknown_route(self):
        status, body = self._call("GET", "/api/search", query="q=T102")
        self.assertTrue(json.loads(body)["results"])
        self.assertTrue(self._call("GET", "/api/nope")[0].startswith("404"))


if __name__ == "__main__":
    unittest.main()
