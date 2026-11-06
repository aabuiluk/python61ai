"""Тести RAG. Запуск з кореня проєкту:

    python -m unittest discover -s tests -t . -v

Тести не викликають OpenAI: ключ у тестах завжди порожній, тож працює
режим «лише фрагменти». Це гарантує, що тести проходять без мережі.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rag.build import build_chunks
from rag.config import PROJECT_DIR, Settings, load_env_file
from rag.search import BM25Index, tokenize
from rag.text import parse_lesson_html
from rag.web import create_app

LESSON = PROJECT_DIR / "data" / "source" / "PythonAI_Author_Lesson45_Lesson46.html"
CHUNKS = PROJECT_DIR / "data" / "chunks.json"


def _settings(chunks_path: Path = CHUNKS, api_key: str = "") -> Settings:
    return Settings(chunks_path=chunks_path, openai_api_key=api_key,
                    openai_model="test-model", use_llm=bool(api_key), top_k=4)


class ParserTests(unittest.TestCase):
    def test_lesson_parses_into_chunks(self):
        chunks = parse_lesson_html(LESSON.read_text(encoding="utf-8"), "lesson")
        self.assertGreater(len(chunks), 20)
        # Меню-навігація і <title> не потрапляють у пошук.
        joined = "\n".join(c.text for c in chunks)
        self.assertNotIn("Зміст практикуму", joined)
        self.assertNotIn("Пари 45–46 · Студент", joined)
        # HTML-теги не лишаються.
        self.assertNotIn("<p>", joined)

    def test_headings_build_path(self):
        html_text = ("<h1>Тема</h1><p>Вступний абзац.</p>"
                     "<h2>Розділ</h2><p>Текст розділу.</p>")
        chunks = parse_lesson_html(html_text, "x")
        paths = {c.text: c.path for c in chunks}
        self.assertEqual(paths["Текст розділу."], "Тема > Розділ")

    def test_code_blocks_keep_newlines(self):
        html_text = "<pre><code>a = 1\nb = 2</code></pre>"
        chunks = parse_lesson_html(html_text, "x")
        self.assertIn("a = 1\nb = 2", chunks[0].text)


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(CHUNKS.read_text(encoding="utf-8"))
        from rag.text import Chunk
        chunks = [Chunk(id=c["id"], path=c["path"], text=c["text"])
                  for c in payload["chunks"]]
        cls.index = BM25Index(chunks)

    def test_tokenize_stems_long_words(self):
        self.assertEqual(tokenize("заявки"), tokenize("заявка"))
        self.assertIn("T101".lower(), tokenize("T101"))

    def test_sla_question_finds_sla_fragment(self):
        results = self.index.search("Який SLA у заявки T101?", top_k=3)
        self.assertTrue(results)
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
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('RAG_TEST_A="from-file"\nRAG_TEST_B=file\n# коментар\n',
                                encoding="utf-8")
            with mock.patch.dict(os.environ, {"RAG_TEST_B": "from-env"}, clear=False):
                load_env_file(env_path)
                self.assertEqual(os.environ["RAG_TEST_A"], "from-file")
                self.assertEqual(os.environ["RAG_TEST_B"], "from-env")
                os.environ.pop("RAG_TEST_A", None)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_settings())

    def _call(self, method, path, body=b"", query=""):
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

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
        self.assertTrue(status.startswith("200"))
        self.assertTrue(data["ok"])
        self.assertGreater(data["chunks"], 20)
        self.assertFalse(data["llm_enabled"])

    def test_ask_without_key_returns_sources(self):
        payload = json.dumps({"question": "Який SLA у заявки T101?"}).encode("utf-8")
        status, body = self._call("POST", "/api/ask", payload)
        data = json.loads(body)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["mode"], "retrieval")
        self.assertTrue(data["sources"])

    def test_ask_rejects_empty_and_long_questions(self):
        status, _ = self._call("POST", "/api/ask", b'{"question": "   "}')
        self.assertTrue(status.startswith("400"))
        long_question = json.dumps({"question": "а" * 301}).encode("utf-8")
        status, _ = self._call("POST", "/api/ask", long_question)
        self.assertTrue(status.startswith("400"))

    def test_ask_rejects_bad_json(self):
        status, _ = self._call("POST", "/api/ask", b"not json")
        self.assertTrue(status.startswith("400"))

    def test_search_endpoint(self):
        status, body = self._call("GET", "/api/search", query="q=T102")
        self.assertTrue(status.startswith("200"))
        self.assertTrue(json.loads(body)["results"])

    def test_unknown_api_route(self):
        status, _ = self._call("GET", "/api/nope")
        self.assertTrue(status.startswith("404"))


if __name__ == "__main__":
    unittest.main()
