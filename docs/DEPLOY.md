# Як працює деплой

Цей документ пояснює, що відбувається після `git push` у `main`. Налаштування з нуля — у [SETUP.md](SETUP.md).

## Ланцюжок

```
push / merge у main
   └─► GitHub Actions  (.github/workflows/deploy.yml)
          1. python -m unittest      → тести; падають — деплою немає
          2. python -m rag.build     → індекс має збігатися з комітом
          3. python scripts/deploy_pa.py
                 ├─ PythonAnywhere API: завантажити app.py, rag/*.py, data/chunks.json
                 ├─ записати .env на сервері (лише якщо є OPENAI_API_KEY)
                 ├─ видалити зайві файли на сервері
                 ├─ записати WSGI у /var/www/…_wsgi.py
                 ├─ PATCH веб-апки: source_directory, python 3.10
                 ├─ POST /webapps/<домен>/reload/
                 └─ GET /api/health до відповіді OK
```

## Чому так

| Рішення | Причина |
|---|---|
| Деплой через API, без `git pull` на сервері | PythonAnywhere не має webhook для git; API доступний з GitHub Actions |
| Нуль сторонніх залежностей | На безплатному тарифі немає сенсу в virtualenv і pip; запуск швидший і надійний |
| Пошук BM25, без embeddings | Не потрібен виклик мережі для кожного питання; безплатний PythonAnywhere часто блокує зовнішні сайти |
| Індекс комітиться (`data/chunks.json`) | Сервер не потребує HTML-уроку й не витрачає CPU на побудову індексу |
| Секрети в GitHub Secrets | У файлах репозиторію секретів немає; значення шифрує GitHub |

## Що деплой чіпає, а що ні

| Частина | Де на сервері | Деплой її змінює? |
|---|---|---|
| Код бота і адмінки | `/home/<логін>/rag_site/` | так, повністю перезаписує |
| `.env` | `/home/<логін>/rag_site/.env` | так, з вашого `.env`/Secrets |
| Індекс з репозиторію | `rag_site/data/` | так |
| БД статистики, оцінки, відповіді викладача | `/home/<логін>/rag_data/` | **ні** |
| Завантажені адміном уроки | `/home/<логін>/rag_data/sources/` | **ні** |
| venv з FastAPI | `/home/<логін>/.virtualenvs/lesson-helper` | ні (лише читається) |

## Секрети і де вони живуть

| Де | Що | Видно кому |
|---|---|---|
| `.env` на вашому ПК | усі значення | тільки вам |
| GitHub Secrets | `PA_USERNAME`, `PA_API_TOKEN`, `PA_DOMAIN`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` | тільки workflow (значення не показуються навіть власнику) |
| `.env` на PythonAnywhere | `OPENAI_API_KEY` (якщо є), `OPENAI_MODEL`, `ADMIN_*`, `RAG_DATA_DIR` | тільки процесу сайту |
| Git-репозиторій | нічого секретного | всі, хто має доступ до репо |

Щоб оновити секрет: змініть `.env` і виконайте `python3 scripts/set_github_secrets.py`.
Щоб оновити ключ OpenAI на сайті — той самий шлях, потім деплой (пуш або запуск Actions вручну).

## Перевірка після деплою

Скрипт наприкінці перевіряє `/api/health` (бот) і чи `/admin` просить вхід (401).
Якщо адмінка відповіла без входу або з 503 — дивіться рядок «Адмінка» у логах деплою.

## Ручний деплой без GitHub

```bash
python3 scripts/check_setup.py
python3 scripts/deploy_pa.py --dry-run
python3 scripts/deploy_pa.py
```

## Що робити, якщо деплой не пройшов

1. **GitHub → Actions** → відкрийте останній запуск → прочитайте крок, який впав.
2. Помилка тестів — виправте код, перевірте локально: `python3 -m unittest discover -s tests -t . -v`.
3. Помилка індексу — `python3 -m rag.build` і закомітьте `data/chunks.json`.
4. Помилка PythonAnywhere API (HTTP 401) — токен недійсний: створіть новий і оновіть `.env` та GitHub Secrets.
5. Сайт не відповідає після деплою — PythonAnywhere → **Web** → **Error log**.

## Обмеження безплатного PythonAnywhere

- **Строк дії веб-апки.** Продовжується кнопкою «Run until 3 months from today» у веб-інтерфейсі. API для цього немає.
- **Ліміт CPU.** Близько 100 CPU-секунд на день. Пошук по одному уроку займає мілісекунди, тож ліміт не має бути проблемою.
- **Зовнішній доступ.** Безплатний тариф пускає лише до дозволених сайтів; `api.openai.com` може бути недоступний. Тоді сайт працює в режимі «лише джерела».
- **Публічний доступ.** Сайт відкритий для всіх. Ліміт 20 запитів до моделі за хвилину захищає квоту OpenAI, якщо ключ увімкнено.

## Оновлення уроку

1. Замініть `data/source/PythonAI_Author_Lesson45_Lesson46.html` (або додайте новий `.html` у ту саму папку).
2. `python3 -m rag.build`
3. `python3 -m unittest discover -s tests -t . -v`
4. Закомітьте `data/source/…` і `data/chunks.json`, відкрийте PR у `main`, змерджте.
5. Деплой запуститься сам.
