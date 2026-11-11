import os
import logging
import sqlite3
import sys
import threading
import re
from datetime import datetime

from db_comments import save_user_comment

from telegram import (
    Update, InputFile,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    ConversationHandler, CallbackContext, CallbackQueryHandler
)
import smtplib
from email.message import EmailMessage
# WeasyPrint is imported lazily inside `generateDocs.generate_pdf` to avoid
# failing startup when native dependencies (cairo/pango/etc.) are missing.
from dotenv import load_dotenv, dotenv_values

# Ensure we prefer a repository .env when present. Compute project root early
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Load environment variables from .env in project root (if present)
load_dotenv(os.path.join(BASE_DIR, '.env'))

# If some vars are missing in the environment, fall back to parsing .env
_dot = dotenv_values(os.path.join(BASE_DIR, '.env'))
for k, v in (_dot.items() if _dot else []):
    if k and (os.getenv(k) is None or os.getenv(k) == '') and v is not None:
        os.environ[k] = v

from ai import ask_gemini
from db import save_user_data, load_user_data
from generateDocs import generate_docx, generate_pdf
from social_links import social_links_handler
from user_analytics import analytics
from feedback import feedback_conversation
from payment_gate import (
    is_payment_required, set_payment_required,
    is_user_paid, mark_user_paid, mark_user_unpaid,
    list_paid_users, show_settings
)

# Read environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
_port_val = os.getenv("PORT")
try:
    PORT = int(_port_val) if _port_val and _port_val.strip() else 8443
except ValueError:
    logging.getLogger(__name__).warning("Invalid PORT value %r, using default 8443", _port_val)
    PORT = 8443
ENV = os.getenv("ENV")
_admin_id_val = os.getenv('ADMIN_USER_ID')
try:
    ADMIN_USER_ID = int(_admin_id_val) if _admin_id_val and _admin_id_val.strip() else None
except ValueError:
    ADMIN_USER_ID = None

# Validate TELEGRAM token early and fail with a clear error if missing
if not TELEGRAM_TOKEN or TELEGRAM_TOKEN.strip() == "":
    logging.getLogger(__name__).error("TELEGRAM_BOT_TOKEN is not set — please add it to .env or pass it to the container")
    sys.exit(1)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States
(
    NAME, EMAIL, PHONE, SUMMARY, EXPERIENCE, EDUCATION,
    SKILLS, LANGUAGES, PROJECTS, REVIEW, WAITING_CALLBACK, PHOTO,
    SELECT_TEMPLATE
) = range(13)

# Contact conversation states (separate from CV creation states)
CONTACT_NAME, CONTACT_EMAIL_C, CONTACT_SUBJECT, CONTACT_MESSAGE, CONTACT_CONFIRM = range(13, 18)

# Initialize base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, 'temp')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

# Ensure directories exist
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# DB setup
def init_db():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "cv_bot.db"))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        data TEXT,
        last_updated TEXT
    )''')
    conn.commit()
    conn.close()

init_db()




# Handlers
def _env_recipients(*names: str) -> list[str]:
    """Return the first non-empty recipient list among the given env vars.

    Values may hold several addresses separated by commas.
    """
    for name in names:
        val = (os.getenv(name) or "").strip()
        if val:
            return [a.strip() for a in val.split(",") if a.strip()]
    return []


def send_email(subject: str, body: str, recipients: list[str], reply_to: str | None = None,
               fallback_log: str = "unsent_emails.log") -> bool:
    """Send a plain-text email using SMTP settings from the environment.

    Port 465 uses implicit SSL, any other port uses STARTTLS when offered.
    On any failure the message is appended to `fallback_log` so nothing is lost.
    Returns True when the email was actually delivered to the SMTP server.
    """
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_pass = (os.getenv("SMTP_PASS") or "").strip()
    try:
        smtp_port = int((os.getenv("SMTP_PORT") or "587").strip())
    except ValueError:
        smtp_port = 587

    sent = False
    if not recipients:
        logger.warning("No recipient email configured for %r (set it in .env)", subject)
    elif not smtp_host:
        logger.warning("SMTP_HOST not set; cannot send %r", subject)
    else:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.getenv("EMAIL_FROM") or smtp_user or "noreply@example.com"
        msg["To"] = ", ".join(recipients)
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        try:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            with server as s:
                s.ehlo()
                if smtp_port != 465 and s.has_extn("starttls"):
                    s.starttls()
                    s.ehlo()
                if smtp_user and smtp_pass:
                    s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            sent = True
            logger.info("Email %r sent to %s", subject, msg["To"])
        except Exception:
            logger.exception("Failed to send email %r via SMTP", subject)

    if not sent:
        try:
            with open(os.path.join(BASE_DIR, fallback_log), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] To: {', '.join(recipients) or '(none)'}\n"
                        f"Subject: {subject}\n{body}\n\n")
        except Exception:
            logger.exception("Failed to persist unsent email locally")
    return sent


def send_email_async(*args, **kwargs):
    """Fire-and-forget wrapper so SMTP latency never blocks the bot."""
    threading.Thread(target=send_email, args=args, kwargs=kwargs, daemon=True).start()


def _describe_user(user) -> str:
    uid = getattr(user, 'id', None)
    username = getattr(user, 'username', '') or ''
    first = getattr(user, 'first_name', '') or ''
    last = getattr(user, 'last_name', '') or ''
    tg_link = f"https://t.me/{username}" if username else "(no username)"
    return (
        f"User id: {uid}\n"
        f"Username: {username}\n"
        f"Name: {first} {last}\n"
        f"Telegram link: {tg_link}\n"
        f"Time (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )


def send_user_info_email(user):
    """Notify the owner (NOTIFY_RECEIVER_EMAIL) that a user ran /start."""
    send_email_async(
        "New CV bot user",
        _describe_user(user),
        _env_recipients("NOTIFY_RECEIVER_EMAIL", "NEW_USER_RECEIVER_EMAIL", "ADMIN_EMAIL"),
        fallback_log="new_users.log",
    )


def send_cv_generated_email(user, cv_names: list[str], template: str = ""):
    """Notify the owner (NOTIFY_RECEIVER_EMAIL) that a CV was generated."""
    body = (
        _describe_user(user)
        + f"Template: {template}\n\nGenerated files:\n" + "\n".join(cv_names) + "\n"
    )
    send_email_async(
        "cv gen",
        body,
        _env_recipients("NOTIFY_RECEIVER_EMAIL", "NEW_USER_RECEIVER_EMAIL", "ADMIN_EMAIL"),
        fallback_log="cv_generations.log",
    )


def start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    analytics.log_action(user_id, "session_started")
    analytics.start_session(user_id)
    # Notify owner with user info (best-effort, runs in background)
    try:
        send_user_info_email(update.effective_user)
    except Exception:
        logger.exception("Failed to start background new-user info email")
    logger.info(f"User {update.effective_user.id} started CV creation")
    context.user_data.clear()
    context.user_data['cv_data'] = {
        "name": "", "email": "", "phone": "", "summary": "",
        "experience": [], "education": [],
        "skills": [], "languages": [], "projects": [],
        "photo_path": "", "template": "professional"
    }
    update.message.reply_text(
    "📸 Please upload a professional profile photo (or type /skip to skip):\n\n"
    "♻️ Already made a CV here? Use /last to reuse your saved details.\n"
    "✅ Use /join to join our Nib International Bank community.\n"
    "📝 Use t.me/ApplicationLetterByTade_bot to generate your application letter.",
    reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Skip Photo", callback_data='skip_photo')]
    ])
)

    # If the user is admin, show admin-only commands in the UI (text list)
    try:
        if _is_admin(update):
            admin_text = (
                "🔐 Admin commands available:\n"
                "/payment_enable - enable payment requirement\n"
                "/payment_disable - disable payment requirement\n"
                "/payment_status - show payment settings\n"
                "/mark_paid <user_id> - mark a user paid\n"
                "/mark_unpaid <user_id> - remove paid status\n"
                "/list_paid - list paid users"
            )
            update.message.reply_text(admin_text)
    except Exception:
        # non-fatal if admin check fails
        pass

    return PHOTO

def handle_comment(update: Update, context: CallbackContext):
    user = update.effective_user

    # If no comment text provided
    if update.message.text == "/comment":
        update.message.reply_text("💬 Please type your comment after the /comment command or just send it now.sample ")
        update.message.reply_text(" sample /comment you made it real cv app")
        return

    comment_text = update.message.text.replace("/comment", "").strip()
    
    if not comment_text:
        update.message.reply_text("❗ You need to provide a comment. Example:\n`/comment This CV bot is amazing!`", parse_mode="Markdown")
        return

    save_user_comment(
        user_id=user.id,
        username=user.username or user.first_name,
        comment=comment_text
    )

    update.message.reply_text("✅ Thank you! Your comment has been saved.")

def select_template(update: Update, context: CallbackContext) -> int:
    """Let user select a template"""
    keyboard = [
        [InlineKeyboardButton("🏢 Professional", callback_data='template_professional'),
         InlineKeyboardButton("🎨 Creative", callback_data='template_creative')],
        [InlineKeyboardButton("💻 Modern Tech", callback_data='template_modern'),
         InlineKeyboardButton("📚 Academic", callback_data='template_academic')],
    ]
    
    update.message.reply_text(
        "🎨 Please select a CV template design:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_TEMPLATE

def receive_photo(update: Update, context: CallbackContext) -> int:
    """Handle photo upload"""
    try:
        photo_file = update.message.photo[-1].get_file()
        filename = f"photo_{update.effective_user.id}.jpg"
        file_path = os.path.join(TEMP_DIR, filename)
        photo_file.download(file_path)
        context.user_data['cv_data']['photo_path'] = file_path
        update.message.reply_text("✅ Photo received. What's your full name?")
        return NAME
    except Exception as e:
        logger.error(f"Photo download failed: {e}")
        update.message.reply_text("❌ Failed to save photo. Please try again or type /skip")
        return PHOTO

def skip_photo(update: Update, context: CallbackContext) -> int:
    """Skip photo upload"""
    query = update.callback_query
    if query:
        query.answer()
        query.edit_message_text("👍 No photo will be included. What's your full name?")
    else:
        update.message.reply_text("👍 No photo will be included. What's your full name?")
    return NAME


def photo_expected(update: Update, context: CallbackContext) -> int:
    """User sent text/other content while we wait for a photo."""
    update.message.reply_text(
        "📸 Please send a photo (as an image, not a file), or type /skip to continue without one."
    )
    return PHOTO


def _split_fields(text: str, count: int) -> list[str]:
    """Split 'a - b - c' style input into exactly `count` fields.

    Separators are a dash/en-dash/em-dash/pipe surrounded by spaces, so values
    like '2020-2023' or 'Front-end' are kept intact. Extra separators are
    merged into the last field (e.g. a description containing ' - ').
    """
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", text.strip())]
    parts = [p for p in parts if p]
    if len(parts) < count:
        # Fallback for inputs written without spaces, e.g. "Dev-Google-2020-desc"
        parts = [p.strip() for p in text.split('-', count - 1) if p.strip()]
    if len(parts) > count:
        parts = parts[:count - 1] + [" - ".join(parts[count - 1:])]
    return parts


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def resume_last_cv(update: Update, context: CallbackContext) -> int:
    """Load the user's previously saved CV data and jump to the review screen."""
    saved = load_user_data(update.effective_user.id)
    if not saved or not isinstance(saved, dict):
        update.message.reply_text("ℹ️ No saved CV found yet. Use /start to create one.")
        return ConversationHandler.END
    analytics.log_action(update.effective_user.id, "cv_resumed")
    cv = {
        "name": "", "email": "", "phone": "", "summary": "",
        "experience": [], "education": [],
        "skills": [], "languages": [], "projects": [],
        "photo_path": "", "template": "professional"
    }
    cv.update(saved)
    # The photo file is removed after each generation; drop stale paths.
    if cv.get("photo_path") and not os.path.exists(cv["photo_path"]):
        cv["photo_path"] = ""
    context.user_data.clear()
    context.user_data['cv_data'] = cv
    update.message.reply_text("♻️ Loaded your last CV. Edit anything below or generate it with a new template.")
    return review(update, context)

def get_name(update: Update, context: CallbackContext) -> int:
    """Get user's name"""
    text = update.message.text.strip()
    context.user_data['cv_data']['name'] = text
    
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text("📧 What's your email address?")
    return EMAIL

def get_email(update: Update, context: CallbackContext) -> int:
    """Get user's email"""
    text = update.message.text.strip()
    if not _EMAIL_RE.match(text):
        update.message.reply_text("❗ That doesn't look like a valid email. Please try again (e.g. name@example.com):")
        return EMAIL
    context.user_data['cv_data']['email'] = text

    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text("📱 What's your phone number?")
    return PHONE

def get_phone(update: Update, context: CallbackContext) -> int:
    """Get user's phone number"""
    text = update.message.text.strip()
    context.user_data['cv_data']['phone'] = text
    
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text("🧠 Please write a short professional summary about yourself:")
    return SUMMARY

def get_summary(update: Update, context: CallbackContext) -> int:
    """Get and enhance professional summary"""
    text = update.message.text.strip()
    polished = ask_gemini(f"Rewrite this professionally in 3-4 sentences:\n{text}")
    context.user_data['cv_data']['summary'] = polished or text
    
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text(
        "💼 Let's add your work experience. Please send in format:\n"
        "Role - Company - Years - Description\n\n"
        "Example:\n"
        "Software Engineer - Google - 2020-2023 - Developed web applications using Python"
    )
    return EXPERIENCE

def get_experience(update: Update, context: CallbackContext) -> int:
    """Add work experience"""
    text = update.message.text.strip()
    parts = _split_fields(text, 4)

    if len(parts) != 4:
        update.message.reply_text(
            "❗ Please use format: Role - Company - Years - Description\n\n"
            "Example:\n"
            "Software Engineer - Google - 2020-2023 - Developed web applications"
        )
      
        return EXPERIENCE
    
    description = ask_gemini(f"Improve this job description professionally:\n{parts[3]}")
    job = {
        'role': parts[0],
        'company': parts[1],
        'years': parts[2] if len(parts) > 2 else "Not specified",
        'description': description or parts[3]
    }
    context.user_data['cv_data']['experience'].append(job)
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Another", callback_data='add_experience')],
        [InlineKeyboardButton("✅ Done", callback_data='finish_experience')]
    ]
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    update.message.reply_text(
        "✅ Experience added!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_CALLBACK

def get_education(update: Update, context: CallbackContext) -> int:
    """Add education"""
    text = update.message.text.strip()
    parts = _split_fields(text, 3)

    if len(parts) != 3:
        update.message.reply_text(
            "❗ Please use format: Degree - Institution - Years\n\n"
            "Example:\n"
            "BSc Computer Science - Harvard University - 2016-2020"
        )
      
        return EDUCATION
    
    edu = {
        'degree': parts[0],
        'institution': parts[1],
        'years': parts[2] if len(parts) > 2 else "Not specified"
    }
    context.user_data['cv_data']['education'].append(edu)
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Another", callback_data='add_education')],
        [InlineKeyboardButton("✅ Done", callback_data='finish_education')]
    ]
    if 'editing_field' in context.user_data:
         del context.user_data['editing_field']
         return review(update, context)
    update.message.reply_text(
        "✅ Education added!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_CALLBACK

def get_skills(update: Update, context: CallbackContext) -> int:
    """Add skills"""
    skills = [s.strip() for s in update.message.text.split(',') if s.strip()]
    context.user_data['cv_data']['skills'] = skills
    
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text(
        "🌐 What languages do you speak? (comma separated)\n"
        "Example: English, Spanish, French"
    )
    return LANGUAGES

def get_languages(update: Update, context: CallbackContext) -> int:
    """Add languages"""
    languages = [l.strip() for l in update.message.text.split(',') if l.strip()]
    context.user_data['cv_data']['languages'] = languages
    
    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)
    
    update.message.reply_text(
        "🛠️ Let's add projects. Please send in format:\n"
        "Name - Description - Technologies\n\n"
        "Example:\n"
        "CV Generator Bot - Telegram bot that creates professional CVs - Python, Telegram API"
    )
    return PROJECTS

def get_projects(update: Update, context: CallbackContext) -> int:
    """Add projects"""
    text = update.message.text.strip()
    parts = _split_fields(text, 3)

    if len(parts) != 3:
        update.message.reply_text(
            "❗ Please use format: Name - Description - Technologies\n\n"
            "Example:\n"
            "CV Generator - Telegram bot that creates CVs - Python, Telegram API"
        )
        return PROJECTS
    
    project = {
        'name': parts[0],
        'description': parts[1],
        'technologies': parts[2]
    }
    context.user_data['cv_data']['projects'].append(project)

    if 'editing_field' in context.user_data:
        del context.user_data['editing_field']
        return review(update, context)

    keyboard = [
        [InlineKeyboardButton("➕ Add Another", callback_data='add_project')],
        [InlineKeyboardButton("✅ Done", callback_data='finish_projects')]
    ]
    update.message.reply_text(
        "✅ Project added!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_CALLBACK

def generate_cv(update: Update, context: CallbackContext) -> int:
    """Generate and send the CV files"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    analytics.log_action(user_id, "cv_generated", {
        "template": context.user_data['cv_data']['template']
    })
    try:
        context.bot.send_message(chat_id=chat_id, text="⏳ Generating your CV, please wait...")
        # Handle both callback and message updates
        if update.callback_query:
            query = update.callback_query
            query.answer()
            chat_id = query.message.chat_id
        else:
            chat_id = update.message.chat_id

        data = context.user_data['cv_data']
        
        # Generate files
        pdf_file = generate_pdf(data)
        docx_file = generate_docx(data)
        
        # Send files
        with open(pdf_file, 'rb') as pdf, open(docx_file, 'rb') as docx:
            context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(pdf),
                filename=f"{data['name'].replace(' ', '_')}_CV.pdf"
            )
            context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(docx),
                filename=f"{data['name'].replace(' ', '_')}_CV.docx"
            )
        
        # Clean up
        try:
            os.remove(pdf_file)
            os.remove(docx_file)
            if 'photo_path' in data and data['photo_path'] and os.path.exists(data['photo_path']):
                os.remove(data['photo_path'])
        except Exception as e:
            logger.warning(f"Error cleaning up files: {e}")
        
        context.bot.send_message(
            chat_id=chat_id,
            text="🎉 Your professional CV is ready!\n\n"
                 "♻️ Use /last to regenerate it with another template.\n"
                 "🆕 Use /start to create another CV.\n"
                 "📝 Use /feedback to rate this CV generation process.\n"
                 "✅ Use /join to join our Nib International Bank community.\n"
                 "✉️ Use t.me/ApplicationLetterByTade_bot to generate your application letter."
        )
        # Notify owner about CV generation (best-effort, runs in background)
        try:
            filenames = [os.path.basename(pdf_file), os.path.basename(docx_file)]
            send_cv_generated_email(update.effective_user, filenames, data.get('template', ''))
        except Exception:
            logger.exception("Failed to start background CV-generation email")
        
        # Save to database
        save_user_data(update.effective_user.id, data)
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.exception(f"CV generation failed: {e}")
        context.bot.send_message(
            chat_id=chat_id,
            text="❌ Failed to generate CV. Please try again."
        )
        return review(update, context)


def callback_handler(update: Update, context: CallbackContext) -> int:
    """Handle all callback queries"""
    query = update.callback_query
    query.answer()
    data = query.data
    
    # Handle template selection
    if data.startswith('template_'):
        template_name = data.split('_')[1]
        context.user_data['cv_data']['template'] = template_name
        query.edit_message_text(f"✅ Selected {template_name.replace('_', ' ')} template")
        return generate_cv(update, context)
    
    # Track which field we're editing
    if data.startswith('edit_'):
        context.user_data['editing_field'] = data
    
    # Handle different button actions
    if data == 'add_experience':
        query.edit_message_text(
            "💼 Add another experience (Role - Company - Years - Description):"
        )
        return EXPERIENCE
        
    elif data == 'finish_experience':
        query.edit_message_text(
            "🎓 Add your education (Degree - Institution - Years):"
        )
        return EDUCATION
        
    elif data == 'add_education':
        query.edit_message_text(
            "🎓 Add another education (Degree - Institution - Years):"
        )
        return EDUCATION
        
    elif data == 'finish_education':
        query.edit_message_text(
            "🛠️ List your skills (comma separated):\n"
            "Example: Python, JavaScript, Project Management"
        )
        return SKILLS
        
    elif data == 'add_project':
        query.edit_message_text(
            "🛠️ Add another project (Name - Description - Technologies):"
        )
        return PROJECTS
        
    elif data == 'finish_projects':
        return review(update, context)
        
    elif data == 'edit_name':
        query.edit_message_text("Please enter your full name:")
        return NAME
        
    elif data == 'edit_email':
        query.edit_message_text("Please enter your email address:")
        return EMAIL
        
    elif data == 'edit_phone':
        query.edit_message_text("Please enter your phone number:")
        return PHONE
        
    elif data == 'edit_summary':
        query.edit_message_text("Please enter your professional summary:")
        return SUMMARY
        
    elif data == 'edit_experience':
        context.user_data['cv_data']['experience'] = []
        query.edit_message_text(
            "💼 Let's re-enter your experiences (Role - Company - Years - Description):"
        )
        return EXPERIENCE
        
    elif data == 'edit_education':
        context.user_data['cv_data']['education'] = []
        query.edit_message_text(
            "🎓 Let's re-enter your education (Degree - Institution - Years):"
        )
        return EDUCATION
        
    elif data == 'edit_skills':
        query.edit_message_text(
            "🛠️ List your skills (comma separated):\n"
            "Example: Python, JavaScript, Project Management"
        )
        return SKILLS
        
    elif data == 'edit_languages':
        query.edit_message_text(
            "🌐 List languages you speak (comma separated):\n"
            "Example: English, Spanish, French"
        )
        return LANGUAGES
        
    elif data == 'edit_projects':
        context.user_data['cv_data']['projects'] = []
        query.edit_message_text(
            "🛠️ Let's re-enter your projects (Name - Description - Technologies):"
        )
        return PROJECTS
        
    elif data == 'generate_cv':
        # First show template selection
        keyboard = [
            [InlineKeyboardButton("🏢 Professional", callback_data='template_professional'),
             InlineKeyboardButton("🎨 Creative", callback_data='template_creative')],
            [InlineKeyboardButton("💻 Modern Tech", callback_data='template_modern'),
             InlineKeyboardButton("📚 Academic", callback_data='template_academic')],
        ]
        
        query.edit_message_text(
            "🎨 Please select a CV template design:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECT_TEMPLATE
        
    elif data == 'cancel':
        query.edit_message_text("❌ CV creation cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
        
    elif data == 'skip_photo':
        return skip_photo(update, context)
        
    else:
        query.edit_message_text("Unknown command, please try again.")
        return WAITING_CALLBACK

def review(update: Update, context: CallbackContext) -> int:
    """Show review of all information with edit options"""
    data = context.user_data['cv_data']
    
    text = "📝 <b>Review Your CV</b>\n\n"
    text += f"👤 <b>Name:</b> {data['name']}\n"
    text += f"📧 <b>Email:</b> {data['email']}\n"
    text += f"📱 <b>Phone:</b> {data['phone']}\n\n"
    
    text += f"🧠 <b>Summary:</b>\n{data['summary']}\n\n"
    
    text += "💼 <b>Experience:</b>\n"
    for i, job in enumerate(data['experience'], 1):
        text += f"{i}. {job['role']} at {job['company']} ({job['years']})\n"
    
    text += "\n🎓 <b>Education:</b>\n"
    for i, edu in enumerate(data['education'], 1):
        text += f"{i}. {edu['degree']} at {edu['institution']} ({edu['years']})\n"
    
    text += f"\n🛠️ <b>Skills:</b>\n{', '.join(data['skills']) if isinstance(data['skills'], list) else ', '.join(sum(data['skills'].values(), []))}\n"
    text += f"\n🌐 <b>Languages:</b>\n{', '.join(data['languages'])}\n"
    
    text += "\n🛠️ <b>Projects:</b>\n"
    for i, p in enumerate(data['projects'], 1):
        text += f"{i}. {p['name']} - {p['technologies']}\n"
    
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Name", callback_data='edit_name'),
         InlineKeyboardButton("✏️ Edit Email", callback_data='edit_email')],
        [InlineKeyboardButton("✏️ Edit Phone", callback_data='edit_phone'),
         InlineKeyboardButton("✏️ Edit Summary", callback_data='edit_summary')],
        [InlineKeyboardButton("✏️ Edit Experience", callback_data='edit_experience')],
        [InlineKeyboardButton("✏️ Edit Education", callback_data='edit_education')],
        [InlineKeyboardButton("✏️ Edit Skills", callback_data='edit_skills')],
        [InlineKeyboardButton("✏️ Edit Languages", callback_data='edit_languages')],
        [InlineKeyboardButton("✏️ Edit Projects", callback_data='edit_projects')],
        [InlineKeyboardButton("✅ Generate CV", callback_data='generate_cv')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Edit existing message if this is an edit return
    if update.callback_query:
        update.callback_query.edit_message_text(
            text,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        return WAITING_CALLBACK
    
    # Send new message if first time review
    update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )
    return WAITING_CALLBACK

def cancel(update: Update, context: CallbackContext) -> int:
    """Cancel the conversation"""
    update.message.reply_text("❌ CV creation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


def contact_start(update: Update, context: CallbackContext) -> int:
    """Start contact flow inside the bot UI."""
    phone = os.environ.get('CONTACT_PHONE', '0949847581')
    recipients = _env_recipients('CONTACT_RECEIVER_EMAIL', 'ADMIN_EMAIL')
    email = recipients[0] if recipients else '(not configured)'
    payment_enabled = is_payment_required()
    cbe_account = '1000261725456'
    amount_etb = 10

    text = f"Contact:\nPhone: {phone}\nEmail: {email}\n"
    if payment_enabled:
        text += f"\nPayment required for unlimited access: pay {amount_etb} ETB to CBE account {cbe_account}.\n"
    text += "\nIf you'd like to send us a message now, please enter your full name (or /cancel to stop):"
    update.message.reply_text(text)
    return CONTACT_NAME


def contact_name(update: Update, context: CallbackContext) -> int:
    context.user_data['contact_name'] = update.message.text.strip()
    update.message.reply_text("Please enter your email address:")
    return CONTACT_EMAIL_C


def contact_email(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    if not _EMAIL_RE.match(text):
        update.message.reply_text("❗ Please enter a valid email address (e.g. name@example.com):")
        return CONTACT_EMAIL_C
    context.user_data['contact_email'] = text
    update.message.reply_text("Subject:")
    return CONTACT_SUBJECT


def contact_subject(update: Update, context: CallbackContext) -> int:
    context.user_data['contact_subject'] = update.message.text.strip()
    update.message.reply_text("Message (type your message and send):")
    return CONTACT_MESSAGE


def contact_message(update: Update, context: CallbackContext) -> int:
    name = context.user_data.get('contact_name', '')
    sender = context.user_data.get('contact_email', '')
    subject = context.user_data.get('contact_subject', '(no subject)')
    body = update.message.text.strip()

    full_body = (
        f"From: {name} <{sender}>\n"
        + _describe_user(update.effective_user)
        + f"\n{body}\n"
    )
    sent = send_email(
        f"[CV Bot contact] {subject}",
        full_body,
        _env_recipients('CONTACT_RECEIVER_EMAIL', 'ADMIN_EMAIL'),
        reply_to=sender or None,
        fallback_log="contact_messages.log",
    )
    if sent:
        update.message.reply_text('✅ Message sent successfully. We will contact you shortly.')
    else:
        update.message.reply_text('⚠️ We could not email your message right now, but it was saved and will be reviewed.')

    # clean contact data
    for k in ['contact_name', 'contact_email', 'contact_subject']:
        context.user_data.pop(k, None)

    return ConversationHandler.END


def contact_cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text('Contact flow cancelled.')
    for k in ['contact_name', 'contact_email', 'contact_subject']:
        context.user_data.pop(k, None)
    return ConversationHandler.END


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if ADMIN_USER_ID is None:
        return False
    try:
        return int(user.id) == ADMIN_USER_ID
    except Exception:
        return False


def admin_payment_enable(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    set_payment_required(True)
    update.message.reply_text('Payment requirement enabled.')


def admin_payment_disable(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    set_payment_required(False)
    update.message.reply_text('Payment requirement disabled.')


def admin_payment_status(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    settings = show_settings()
    users = list_paid_users()
    update.message.reply_text(
        f"💳 Payment required: {'ON' if settings.get('enabled') else 'OFF'}\n"
        f"👥 Paid users: {len(users)}\n"
        f"🗄️ Storage: {settings.get('storage', 'unknown')}"
    )


def admin_mark_paid(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    args = context.args
    if not args:
        update.message.reply_text('Usage: /mark_paid <user_id>')
        return
    uid = args[0]
    mark_user_paid(uid)
    update.message.reply_text(f'Marked {uid} as paid')


def admin_mark_unpaid(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    args = context.args
    if not args:
        update.message.reply_text('Usage: /mark_unpaid <user_id>')
        return
    uid = args[0]
    mark_user_unpaid(uid)
    update.message.reply_text(f'Marked {uid} as unpaid')


def admin_list_paid(update: Update, context: CallbackContext):
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    users = list_paid_users()
    update.message.reply_text('\n'.join(users) if users else '(no paid users)')


def admin_test_email(update: Update, context: CallbackContext):
    """Admin: send a test email to every configured recipient to verify SMTP."""
    if not _is_admin(update):
        update.message.reply_text('Unauthorized.')
        return
    notify = _env_recipients("NOTIFY_RECEIVER_EMAIL", "NEW_USER_RECEIVER_EMAIL", "ADMIN_EMAIL")
    contact = _env_recipients("CONTACT_RECEIVER_EMAIL", "ADMIN_EMAIL")
    recipients = sorted(set(notify + contact))
    if not recipients:
        update.message.reply_text('❌ No recipient email configured. Set NOTIFY_RECEIVER_EMAIL / CONTACT_RECEIVER_EMAIL in .env')
        return
    update.message.reply_text(f'📤 Sending test email to: {", ".join(recipients)} ...')
    ok = send_email("CV bot SMTP test", "SMTP is configured correctly.\n\n" + _describe_user(update.effective_user), recipients)
    update.message.reply_text('✅ Test email sent.' if ok else '❌ Sending failed — check SMTP_* settings and the server logs.')


def help_handler(update: Update, context: CallbackContext):
    text = (
        "Available commands:\n"
        "/start - begin CV creation\n"
        "/last - reuse your last saved CV (change template / edit)\n"
        "/skip - skip the photo step\n"
        "/cancel - cancel the current step\n"
        "/contact - send us a message\n"
        "/comment - leave a comment\n"
        "/feedback - provide feedback\n"
        "/help - show this message\n"
    )
    try:
        update.message.reply_text(text)
        if _is_admin(update):
            admin_text = (
                "\nAdmin commands:\n"
                "/payment_enable\n"
                "/payment_disable\n"
                "/payment_status\n"
                "/mark_paid <user_id>\n"
                "/mark_unpaid <user_id>\n"
                "/list_paid\n"
                "/test_email - verify SMTP and recipient emails\n"
            )
            update.message.reply_text(admin_text)
    except Exception:
        pass

def error_handler(update: Update, context: CallbackContext):
    """Log errors"""
    logger.error("Update %s caused error", update, exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            update.effective_message.reply_text(
                "❌ An error occurred. Please try again or /cancel to start over."
            )
    except Exception:
        logger.exception("Failed to notify user about error")

def main():
    """Start the bot"""
    # Connect persistent storage early so the startup log shows its status
    import pg_store
    if not pg_store.available():
        logger.warning("Persistent storage: Postgres unavailable - using local files (data resets on redeploy)")
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(feedback_conversation)
    # Add social links handlers
    for handler in social_links_handler():
        dp.add_handler(handler)
      # 🔽 Register the comment handler
    dp.add_handler(CommandHandler("comment", handle_comment))
    # Contact flow inside Telegram (collects message and sends via SMTP or saves locally)
    contact_conv = ConversationHandler(
        entry_points=[CommandHandler('contact', contact_start)],
        states={
            CONTACT_NAME: [MessageHandler(Filters.text & ~Filters.command, contact_name)],
            CONTACT_EMAIL_C: [MessageHandler(Filters.text & ~Filters.command, contact_email)],
            CONTACT_SUBJECT: [MessageHandler(Filters.text & ~Filters.command, contact_subject)],
            CONTACT_MESSAGE: [MessageHandler(Filters.text & ~Filters.command, contact_message)],
        },
        fallbacks=[CommandHandler('cancel', contact_cancel)],
        allow_reentry=True
    )
    dp.add_handler(contact_conv)
    # Admin payment handlers (restricted by ADMIN_USER_ID env var)
    dp.add_handler(CommandHandler('payment_enable', admin_payment_enable))
    dp.add_handler(CommandHandler('payment_disable', admin_payment_disable))
    dp.add_handler(CommandHandler('payment_status', admin_payment_status))
    dp.add_handler(CommandHandler('mark_paid', admin_mark_paid))
    dp.add_handler(CommandHandler('mark_unpaid', admin_mark_unpaid))
    dp.add_handler(CommandHandler('list_paid', admin_list_paid))
    dp.add_handler(CommandHandler('test_email', admin_test_email))
    # Help command (shows admin commands to admin only)
    dp.add_handler(CommandHandler('help', lambda u, c: help_handler(u, c)))
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('last', resume_last_cv),
        ],
        states={
            PHOTO: [
                MessageHandler(Filters.photo, receive_photo),
                CallbackQueryHandler(skip_photo, pattern='^skip_photo$'),
                CommandHandler('skip', skip_photo),
                MessageHandler(~Filters.command, photo_expected),
            ],
            NAME: [MessageHandler(Filters.text & ~Filters.command, get_name)],
            EMAIL: [MessageHandler(Filters.text & ~Filters.command, get_email)],
            PHONE: [MessageHandler(Filters.text & ~Filters.command, get_phone)],
            SUMMARY: [MessageHandler(Filters.text & ~Filters.command, get_summary)],
            EXPERIENCE: [MessageHandler(Filters.text & ~Filters.command, get_experience)],
            EDUCATION: [MessageHandler(Filters.text & ~Filters.command, get_education)],
            SKILLS: [MessageHandler(Filters.text & ~Filters.command, get_skills)],
            LANGUAGES: [MessageHandler(Filters.text & ~Filters.command, get_languages)],
            PROJECTS: [MessageHandler(Filters.text & ~Filters.command, get_projects)],
            WAITING_CALLBACK: [CallbackQueryHandler(callback_handler)],
            REVIEW: [MessageHandler(Filters.text & ~Filters.command, review)],
            SELECT_TEMPLATE: [CallbackQueryHandler(callback_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    dp.add_handler(conv_handler)
    dp.add_error_handler(error_handler)

    if ENV == "production":
        # Webhook configuration for production
        updater.start_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
        )
        logger.info("Webhook server started in production mode")
    else:
        # Polling for development
        updater.start_polling()
        logger.info("Polling server started in development mode")

    updater.idle()

if __name__ == '__main__':
    main()