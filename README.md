# AI Real CV Generator

A Telegram bot that helps users build professional CVs and exports PDF and DOCX files. Supports local development (polling) and production (webhook) modes.

## Quick overview
- Run with Docker (recommended) or locally with Python 3.11.
- Add secrets into a `.env` placed in the project root.

## Environment (.env)
Create a `.env` file at the project root. Required keys:

- `TELEGRAM_BOT_TOKEN` – your Telegram bot token.
- `GEMINI_API_KEY` – AI/key value used by the app.
- `ADMIN_USER_ID` – numeric Telegram user id of the admin (used for payment admin commands).

Optional keys:

- `ENV` – `development` (default polling) or `production` (webhook).
- `PORT` – port for webhook mode (default `8443` if unset or invalid).
- `WEBHOOK_URL` – base URL for webhook mode.
- `DATABASE_URL` – optional Postgres URL; app will run without DB if DB is unavailable.

See `.env.example` for an example.

## Admin / Payment commands
The bot uses a small independent payment gate. Admin is identified by the numeric `ADMIN_USER_ID` only.

Admin Telegram commands (send from the admin Telegram account):

- `/payment_enable` – enable the payment requirement globally.
- `/payment_disable` – disable the payment requirement.
- `/payment_status` – show current payment settings + paid user count.
- `/mark_paid <user_id>` – mark a user id as paid.
- `/mark_unpaid <user_id>` – mark a user id as unpaid.
- `/list_paid` – list paid user ids.

Behavior note: the payment gate is independent (zero-regression). To enforce payment in a handler, the code checks `payment_gate.is_payment_required()` and `payment_gate.is_user_paid(user_id)`; the repo includes `payment_gate.py` and `admin_payment.py` for local admin via CLI.

## Run with Docker

Build and run (from project root):

```powershell
docker build -t ai_real_cv_generator .
docker run -d --name cvbot --env-file C:\path\to\.env -p 3000:3000 ai_real_cv_generator
docker logs -f cvbot
```

For development (mount project):

```powershell
docker run -d --name cvbot -v ${PWD}:/app -p 3000:3000 ai_real_cv_generator
docker logs -f cvbot
```

## Run locally (no Docker)

Install system dependencies required by WeasyPrint (cairo/pango/gdk-pixbuf) and then:

```bash
python3.11 -m venv .venv
source .venv/bin/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
python bot.py
```

## Admin CLI (local)

You can also manage payment configuration locally without using Telegram via the included CLI:

```bash
python admin_payment.py status
python admin_payment.py enable
python admin_payment.py disable
python admin_payment.py mark-paid 123456789
python admin_payment.py list-paid
```

## Security
- Do not commit `.env` with secrets. Use a secrets manager in production.
