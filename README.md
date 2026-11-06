# python61ai — RAG по конспекту уроків 45–46

Невеликий RAG-застосунок: ставите питання — отримуєте відповідь, що спирається на
конспект пари («Пари 45–46 · Від LCEL до Tools і перевіреного workflow»).

- **Сайт:** https://csctemplate.pythonanywhere.com/
- **Без ключа OpenAI** — показує найбільш релевантні фрагменти уроку (пошук BM25).
- **З ключем OpenAI** — формує відповідь моделі, спираючись лише на ці фрагменти.
- **Жодних сторонніх бібліотек** — працює на безплатному PythonAnywhere.

## Швидкий старт

```bash
python3 -m unittest discover -s tests -t . -v   # тести
python3 app.py                                   # http://127.0.0.1:8000
```

## Документація

| Документ | Про що |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Налаштування з нуля: `.env`, PythonAnywhere, GitHub Secrets, перший деплой — крок за кроком |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Як працює автодеплой при пуші в `main`, обмеження безплатного тарифу, troubleshooting |

## Структура

```
app.py                       точка входу (локально і на PythonAnywhere)
rag/                         код RAG: розбиття HTML, BM25, OpenAI, WSGI-застосунок
data/source/                 HTML-конспект уроку (джерело)
data/chunks.json             індекс фрагментів (будується з data/source)
scripts/                     налаштування та деплой (check_setup, deploy_pa, …)
deploy/pa_wsgi.py.tmpl       шаблон WSGI-файлу для PythonAnywhere
.github/workflows/deploy.yml автодеплой у main
.env.example                 шаблон локальних секретів (сам .env не комітиться)
tests/                       тести без мережі
```

## Секрети

У репозиторії секретів **немає**. Токени і ключі живуть у локальному `.env`
(ігнорується Git) і в GitHub Secrets (передає скрипт `scripts/set_github_secrets.py`).
Детально — [docs/SETUP.md](docs/SETUP.md), крок 2 і крок 6.
