# Налаштування з нуля: локально → PythonAnywhere → GitHub

Цей документ веде вас крок за кроком. Кожен крок має:
- **Що** робимо;
- **Навіщо** (щоб було зрозуміло, а не просто «натисни»);
- **Як** — команди або дії в інтерфейсі.

Час: близько 30–40 хвилин, якщо все встановлено.

Схема того, що ми будуємо:

```
data/source/урок.html ──(python -m rag.build)──> data/chunks.json  (індекс, комітиться)
                                                       │
 ваш ПК: .env (секрети, НЕ в Git)                      │
    │  python scripts/set_github_secrets.py             │
    ▼                                                   │
 GitHub Secrets ──► push у main ──► GitHub Actions ─────┤
                                      │ тести           │
                                      ▼                 ▼
                         scripts/deploy_pa.py ──API──► PythonAnywhere
                                                        csctemplate.pythonanywhere.com
```

---

## Крок 0. Що має бути встановлено

**Що:** Python 3.10+, git, GitHub CLI (`gh`).

**Навіщо:** Python запускає скрипти; git працює з репозиторієм; `gh` передає секрети в GitHub.

**Як:**

macOS (Terminal):

```bash
# Перевірка версій
python3 --version     # має бути 3.10 або новіше
git --version

# GitHub CLI (потрібен лише для кроку 6)
brew install gh
```

Windows (PowerShell):

```powershell
python --version
git --version
winget install GitHub.cli
```

> **Примітка для macOS з python.org:** якщо скрипт пише `CERTIFICATE_VERIFY_FAILED`,
> відкрийте папку `/Applications/Python 3.x/` і запустіть `Install Certificates.command`.
> Або: `python3 -m pip install certifi` — скрипти підхоплять сертифікати автоматично.

---

## Крок 1. Отримати репозиторій

**Що:** клонуємо репозиторій `aabuiluk/python61ai` на свій комп'ютер.

**Навіщо:** скрипти, які ми створили, мають бути локально — саме з вашого ПК запускається налаштування.

```bash
git clone https://github.com/aabuiluk/python61ai.git
cd python61ai
```

Якщо репозиторій уже є — просто перейдіть у його папку і виконайте `git pull`.

---

## Крок 2. Створити локальний файл `.env` із секретами

**Що:** копіюємо шаблон `.env.example` у `.env` і заповнюємо значення.

**Навіщо:** секрети (токен PythonAnywhere, ключ OpenAI) потрібні скриптам, але **не можуть** лежати в Git. Файл `.env` ігнорується Git-ом (рядок `.env` у `.gitignore`), тому він лишається тільки на вашому комп'ютері.

```bash
cp .env.example .env          # macOS / Linux
# Windows PowerShell:  Copy-Item .env.example .env
```

Відкрийте `.env` у редакторі і заповніть:

| Змінна | Звідки взяти | Обов'язкова |
|---|---|---|
| `PA_USERNAME` | Ваш логін на PythonAnywhere (наприклад `csctemplate`) | так |
| `PA_API_TOKEN` | PythonAnywhere → **Account** → розділ **API token** → *Create a new API token* | так |
| `PA_DOMAIN` | `<логін>.pythonanywhere.com`, можна залишити порожнім | ні |
| `OPENAI_API_KEY` | platform.openai.com → API keys | ні (без нього сайт працює в режимі «лише джерела») |
| `OPENAI_MODEL` | Модель, яка є у вашому проєкті OpenAI | ні (за замовчуванням `gpt-4.1-mini`) |
| `ADMIN_USERNAME` | Логін до адмінки `/admin` (за замовчуванням `admin`) | ні |
| `ADMIN_PASSWORD` | Пароль до адмінки. Згенерований пароль уже в `.env`; при бажанні замініть своїм, довгим | так, інакше адмінка вимкнена |

> **Пароль від PythonAnywhere в `.env` не потрібен.** Скрипти працюють тільки з API-токеном.
> Пароль вводиться лише у веб-інтерфейсі, і ми його нікуди не зберігаємо.

Перевірте, що `.env` не потрапляє в Git:

```bash
git check-ignore -v .env     # має вивести рядок із правилом .gitignore
```

---

## Крок 3. Перевірити налаштування

**Що:** запускаємо перевірку. Вона нічого не змінює — лише показує, що готово.

**Навіщо:** краще знайти помилку на цьому кроці, ніж після деплою.

```bash
python3 scripts/check_setup.py
```

Очікуваний результат: рядки `OK` для `.env`, токена PythonAnywhere та інструментів.
Рядки `ПОМИЛКА` мають підказку, що зробити. Сайт на цьому етапі може бути
`ще не відповідає` — це нормально.

---

## Крок 4. Перевірити і (за потреби) перебудувати індекс уроку

**Що:** `data/source/PythonAI_Author_Lesson45_Lesson46.html` (ваш конспект пари) розбивається на фрагменти у `data/chunks.json`.

**Навіщо:** сервер на PythonAnywhere читає готовий індекс і не потребує HTML-файлу. Індекс комітиться в Git, тож він завжди відповідає уроку.

```bash
python3 -m rag.build
python3 -m unittest discover -s tests -t . -v     # тести мають пройти
```

Якщо ви змінили HTML-урок — повторіть `python3 -m rag.build` і закомітьте `data/chunks.json`.
GitHub Actions перевіряє це автоматично й не розгортає сайт з застарілим індексом.

Локальний запуск сайту для перевірки (без PythonAnywhere):

```bash
python3 app.py          # відкрийте http://127.0.0.1:8000
```

---

## Крок 5. Очистити старий сайт на PythonAnywhere

**Що:** видаляємо старі файли (папка `lesson_helper` і скрипт `lesson_helper_extract.sh`).

**Навіщо:** на сайті має бути тільки новий застосунок. Домашні файли (`.bashrc`, віртуальні середовища, `README.txt`) не чіпаємо.

Спочатку подивіться, що буде видалено (нічого не змінюється):

```bash
python3 scripts/pa_clear_old_site.py
```

Якщо список правильний — видаліть:

```bash
python3 scripts/pa_clear_old_site.py --yes
```

Якщо на якомусь етапі пише «папку не видалено через API» — видаліть порожню папку
вручну: PythonAnywhere → **Files** → наведіть на папку → значок смітника.

---

## Крок 6. Передати секрети в GitHub

**Що:** копіюємо значення з `.env` у GitHub Secrets (`PA_USERNAME`, `PA_API_TOKEN`, `PA_DOMAIN`, `OPENAI_API_KEY`, `OPENAI_MODEL`).

**Навіщо:** GitHub Actions має знати токен, щоб деплоїти. Секрети GitHub зберігає в зашифрованому вигляді, їх не видно в коді й логах.

Один раз увійдіть у GitHub CLI:

```bash
gh auth login
# Оберіть: GitHub.com → HTTPS → Login with a web browser
```

Далі — один скрипт:

```bash
python3 scripts/set_github_secrets.py --dry-run   # покаже імена, нічого не передасть
python3 scripts/set_github_secrets.py             # передасть
```

Перевірте в браузері: GitHub → репозиторій → **Settings → Secrets and variables → Actions**. Мають бути назви секретів (значень ви не побачите — це нормально).

---

## Крок 7. Перший деплой (вручну, один раз)

**Що:** викладаємо застосунок на PythonAnywhere з вашого ПК.

**Навіщо:** перевірити, що все працює, до того як GitHub почне деплоїти автоматично.

```bash
python3 scripts/deploy_pa.py --dry-run   # план без змін
python3 scripts/deploy_pa.py             # справжній деплой
```

Скрипт:
1. завантажує `app.py`, `rag/*.py`, `data/chunks.json`;
2. записує `.env` на сервері (лише якщо є `OPENAI_API_KEY`);
3. записує WSGI-файл і вказує веб-апці папку `/home/<логін>/rag_site`;
4. перезапускає веб-апку і чекає відповіді `/api/health`.

У кінці має бути: `Готово: https://csctemplate.pythonanywhere.com/`.

### Важливо про строк дії безплатного акаунту

На безплатному PythonAnywhere веб-апка вимикається, коли закінчується строк (у вас —
**15 листопада 2026**). Продовжити можна лише кнопкою у веб-інтерфейсі:

1. Увійдіть на pythonanywhere.com (саме у веб-інтерфейсі, вхід по паролю).
2. Вкладка **Web** → блок **Run until 3 months from today** → натисніть кнопку.

API для цього немає, тому скрипти цього не роблять.

---

## Крок 8. Автодеплой у `main`

**Що:** файл `.github/workflows/deploy.yml` уже в репозиторії. Після кроку 6 GitHub сам викладатиме сайт при кожному пуші в `main`.

**Навіщо:** щоб не деплоїти вручну. Пуш → тести → деплой.

Як це перевірити:

```bash
git checkout -b my-change
# ...зробіть правку...
git commit -am "Опис змін"
git push -u origin my-change     # відкрийте PR і змерджте в main
```

Після мерджа в `main` відкрийте **Actions** на GitHub. Має з'явитися запуск
**Deploy to PythonAnywhere**. Зелена галочка — сайт оновлено.

Ручний запуск: **Actions → Deploy to PythonAnywhere → Run workflow**.

---

## Крок 8а. Адмінка

Адреса: `https://csctemplate.pythonanywhere.com/admin`. Логін і пароль — у `.env`
(`ADMIN_USERNAME`, `ADMIN_PASSWORD`). Опис усіх функцій — [ADMIN.md](ADMIN.md).

Адмінці потрібні пакети FastAPI, які вже є у venv PythonAnywhere
`lesson-helper`. Якщо venv немає, створіть його в PythonAnywhere (Consoles → Bash):

```bash
mkdir -p ~/.virtualenvs && python3.10 -m venv ~/.virtualenvs/lesson-helper
~/.virtualenvs/lesson-helper/bin/pip install -r ~/rag_site/requirements.txt
```

Без venv бот працює, а `/admin` відповідає 503 з поясненням.

## Крок 9. Перевірка роботи сайту

Відкрийте `https://csctemplate.pythonanywhere.com/` і поставте питання, наприклад:

- «Який SLA у заявки T101?» — очікуємо фрагмент із блоку 5 або 9;
- «Чим відрізняється chain від agent?» — фрагмент із блоку 1;
- «Рецепт борщу» — має прийти відповідь, що в уроці такого немає.

Швидка перевірка без браузера:

```bash
curl -s https://csctemplate.pythonanywhere.com/api/health
curl -s -X POST https://csctemplate.pythonanywhere.com/api/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Який SLA у заявки T101?"}'
```

Якщо ключ OpenAI заданий, але на безплатному PythonAnywhere `api.openai.com`
недоступний, сайт покаже фрагменти й поле `llm_error` з поясненням. Це очікувана поведінка.

---

## Типові проблеми

| Симптом | Причина | Що робити |
|---|---|---|
| `Токен PythonAnywhere не приймається` | Токен скопійовано з помилкою або видалено | Створіть новий у PythonAnywhere → Account і оновіть `.env` і GitHub Secrets (крок 6) |
| `CERTIFICATE_VERIFY_FAILED` на macOS | Python.org без системних сертифікатів | `Install Certificates.command` або `pip install certifi` |
| Сайт показує 502 / «Something went wrong» | Помилка в WSGI або коді | PythonAnywhere → **Web** → **Error log** |
| Сайт відповідає старою версією | Запуск деплою не відбувся або зупинився на тестах | GitHub → **Actions**: прочитайте лог кроку |
| Actions: «data/chunks.json не відповідає HTML» | Змінили урок, але не перебудували індекс | `python3 -m rag.build`, `git add data/chunks.json`, коміт |
| Сайт не відкривається після 15.11.2026 | Строк безплатної веб-апки минув | Крок 7: кнопка «Run until 3 months from today» |
| `Немає доступу до OpenAI` у відповіді | Безплатний PythonAnywhere не пускає до OpenAI | Це обмеження тарифу: сайт працює в режимі джерел |

Більше деталей про те, як працює деплой, — у [DEPLOY.md](DEPLOY.md).
