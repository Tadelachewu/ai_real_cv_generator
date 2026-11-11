# AI Real CV Generator

A Telegram bot that guides users step by step through building a professional CV, polishes the text with AI (Google Gemini), and sends back ready-to-use **PDF** and **DOCX** files in one of four designs.

- Runs in **polling** mode for local development and **webhook** mode in production (Render).
- Persistent data (admin settings, paid users, saved CVs, analytics, feedback) is stored in **Postgres (Neon)**, with local-file fallbacks so the bot keeps working if the database is down.

---

## Features

### Building a CV (`/start`)
The bot asks for each section in order:

| Step | What the user sends | Notes |
|---|---|---|
| 1. Photo | A profile photo, or `/skip` / the **Skip Photo** button | Shown on the PDF and DOCX. |
| 2. Name | Full name | |
| 3. Email | Email address | Validated; the bot asks again if it looks invalid. |
| 4. Phone | Phone number | |
| 5. Summary | A few sentences about yourself | **Rewritten professionally by AI.** |
| 6. Experience | `Role - Company - Years - Description` | Repeatable (**➕ Add Another** / **✅ Done**). The description is **improved by AI**. Dates such as `2020-2023` are kept intact. |
| 7. Education | `Degree - Institution - Years` | Repeatable. |
| 8. Skills | Comma-separated list | |
| 9. Languages | Comma-separated list | |
| 10. Projects | `Name - Description - Technologies` | Repeatable. |

Then a **review screen** shows everything, with buttons to edit any section, **✅ Generate CV**, or **❌ Cancel**.

After **Generate CV** the user picks a template:

| Template | File |
|---|---|
| 🏢 Professional | `templates/professional_cv.html` |
| 🎨 Creative | `templates/creative_cv.html` |
| 💻 Modern Tech | `templates/modern_cv.html` |
| 📚 Academic | `templates/academic_cv.html` |

The bot sends the CV as a PDF (rendered with WeasyPrint) and a DOCX (python-docx), saves the CV data so it can be reused, and deletes the temporary files.

If the AI service is unavailable, the user's original text is used, so a CV can always be generated.

### User commands

| Command | What it does |
|---|---|
| `/start` | Start building a new CV. |
| `/last` | Reload your last saved CV, edit it, or regenerate it with a different template. |
| `/skip` | Skip the photo step. |
| `/cancel` | Cancel the current flow. |
| `/contact` | Shows contact details (and payment info when payment is enabled), then lets the user send a message that is emailed to the owner. |
| `/feedback` | Rate the bot 1–5 stars and/or leave a written comment (stored in the database). |
| `/comment <text>` | Leave a quick comment (stored in the database). |
| `/join` | Links to the community / social pages. |
| `/help` | Lists the commands (admins also see the admin commands). |

### Owner notifications (email)
The owner receives an email:
- when anyone runs `/start` (user id, username, name, Telegram link), sent to `NOTIFY_RECEIVER_EMAIL`;
- when a CV is generated (user, template, file names), sent to `NOTIFY_RECEIVER_EMAIL`;
- when a user sends a `/contact` message, sent to `CONTACT_RECEIVER_EMAIL`, with **Reply-To** set to the user's email.

Emails are sent in the background so they never slow the bot down. If sending fails, the message is appended to a local log file (`new_users.log`, `cv_generations.log`, `contact_messages.log`) instead of being lost.

### Admin commands
The admin is the Telegram account whose numeric id is in `ADMIN_USER_ID`. Other users get `Unauthorized.`

| Command | What it does |
|---|---|
| `/payment_enable` | Turn the payment requirement on. |
| `/payment_disable` | Turn it off. |
| `/payment_status` | Show whether payment is on, the number of paid users, and the storage in use (`postgres` or `local files`). |
| `/mark_paid <user_id>` | Mark a Telegram user as paid. |
| `/mark_unpaid <user_id>` | Remove paid status. |
| `/list_paid` | List paid user ids. |
| `/test_email` | Send a test email to every configured recipient to verify the SMTP settings. |

> **Note:** the payment flag is currently informational. `/contact` shows the payment instructions when it is on, but CV generation is not blocked for unpaid users. To enforce it, check `payment_gate.is_payment_required()` and `payment_gate.is_user_paid(user_id)` before `generate_cv`.

The same settings can be managed from a terminal with the admin CLI (it uses the same database):

```bash
python admin_payment.py status
python admin_payment.py enable
python admin_payment.py disable
python admin_payment.py mark-paid 123456789
python admin_payment.py mark-unpaid 123456789
python admin_payment.py list-paid
```

---

## Data storage

All persistent data lives in the Postgres database configured by `DATABASE_URL` (Neon). Tables are created automatically on startup.

| Table | Contents | Module |
|---|---|---|
| `app_settings` | Admin settings, e.g. `payment_required` | `pg_store.py`, `payment_gate.py` |
| `paid_users` | User ids marked paid by the admin | `pg_store.py`, `payment_gate.py` |
| `saved_cvs` | Each user's last CV (used by `/last`) | `pg_store.py`, `db.py` |
| `users`, `user_sessions`, `user_actions` | Usage analytics | `user_analytics.py` |
| `user_feedback` | `/feedback` ratings and comments | `user_analytics.py`, `feedback.py` |
| `comments` | `/comment` messages | `db_comments.py` |

**Why a database:** Render's disk is wiped on every deploy and restart, so anything stored in local files (the old `config/*.json` and `cv_bot.db`) would be lost.

**Fallback:** if the database cannot be reached, the bot keeps running and uses local files (`config/payment_settings.json`, `config/paid_users.json`, `cv_bot.db`). It retries the database every 60 seconds. On the first successful connection, any existing local data is imported once; rows already in the database are never overwritten.

---

## Configuration

Copy `.env.example` to `.env` for local development. In production, set the same variables in **Render → Environment**. Variables set there take priority over `.env`.

> ⚠️ **Never commit `.env`.** It is listed in `.gitignore` and `.dockerignore`.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather. |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key used for AI text polishing. |
| `ADMIN_USER_ID` | ✅ | Numeric Telegram id of the admin. |
| `DATABASE_URL` | ✅ | Postgres connection string (Neon pooled URL). |
| `ENV` | | `production` uses webhook mode; anything else uses polling. |
| `WEBHOOK_URL` | prod | Public base URL, e.g. `https://ai-real-cv-generator.onrender.com`. |
| `PORT` | | Port for the webhook server (Render sets this automatically; default `8443`). |
| `SMTP_HOST` / `SMTP_PORT` | email | e.g. `smtp.gmail.com` / `587` (STARTTLS) or `465` (SSL). |
| `SMTP_USER` / `SMTP_PASS` | email | SMTP login; for Gmail use an **App Password**. |
| `EMAIL_FROM` | | Sender address (defaults to `SMTP_USER`). |
| `NOTIFY_RECEIVER_EMAIL` | email | Receives `/start` and CV-generated notifications. Comma-separate for several addresses. |
| `CONTACT_RECEIVER_EMAIL` | email | Receives `/contact` messages; also shown to users in `/contact`. |
| `CONTACT_PHONE` | | Phone number shown in `/contact`. |
| `DATABASE_URL_UNPOOLED`, `NEON_*` | | Written by `neon init`; not used by the bot. |

---

## Running

### Locally (polling)
Requires Python 3.11 and WeasyPrint's system libraries (cairo, pango, gdk-pixbuf). On Windows, Docker is the easiest route.

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip wheel "setuptools<81"
pip install -r requirements.txt
python bot.py
```

### Docker

```bash
docker build -t ai_real_cv_generator .
docker run -d --name cvbot --env-file .env -p 3000:3000 ai_real_cv_generator
docker logs -f cvbot
```

### Deploying on Render
1. Create a **Web Service** from this repo; Render builds it with the `Dockerfile`.
2. Add every variable from the table above in **Environment**, with `ENV=production` and `WEBHOOK_URL` set to the service URL.
3. Deploy. A healthy startup log contains:
   - `Persistent storage: Postgres ready (app_settings, paid_users, saved_cvs)`
   - `Analytics enabled (database connected)`
   - `Webhook server started in production mode`
4. From the admin account, send `/test_email` and `/payment_status` to confirm email and storage.

> **`setuptools` must stay below 81.** python-telegram-bot 13.15 depends on APScheduler 3.6.3, which imports `pkg_resources`; setuptools 81+ removed it, which crashes the bot with `ModuleNotFoundError: No module named 'pkg_resources'`. The Dockerfile pins this and checks the imports during the build. The `pkg_resources is deprecated` warning in the logs is harmless.

---

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Entry point: Telegram handlers, CV conversation, contact flow, admin commands, email notifications. |
| `ai.py` | Gemini API calls used to polish text. |
| `generateDocs.py` | Renders the PDF (Jinja2 + WeasyPrint) and the DOCX. |
| `templates/` | HTML templates for the four CV designs (plus `contact.html` for the web app). |
| `pg_store.py` | Postgres connection, schema creation and legacy-data import for settings, paid users and saved CVs. |
| `payment_gate.py` | Payment flag and paid-user API (database first, local JSON fallback). |
| `db.py` | Saved-CV API used by `/last` (database first, SQLite/JSON fallback). |
| `user_analytics.py` | Analytics, sessions and feedback tables. |
| `feedback.py` | `/feedback` conversation. |
| `db_comments.py` | `/comment` storage. |
| `social_links.py` | `/join` links. |
| `admin_payment.py` | Admin CLI for payment settings. |
| `web_app.py` | Optional Flask contact page (`/contact`), not started by `bot.py`. |
| `imghdr.py` | Shim for the `imghdr` module removed from newer Python versions. |

## Security
- Never commit `.env` or paste secrets into issues or chats. If a secret was ever committed, replace it at the provider; removing it from the repo does not remove it from git history.
- Keep `ADMIN_USER_ID` correct: it is the only check for admin commands.
