import os
import logging
import sqlite3
import sys


from docx import Document

from telegram.ext import ContextTypes
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
from db import save_user_data
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
def send_user_info_email(user):
    """Send Telegram user id and username to configured receiver (or default).

    Defaults to tade2024bdulin@gmail.com. Uses SMTP settings from the environment
    and falls back to logging to `new_users.log` on failure.
    """
    receiver = os.getenv("NEW_USER_RECEIVER_EMAIL") or "tade2024bdulin@gmail.com"
    try:
        msg = EmailMessage()
        msg["Subject"] = "New CV bot user"
        msg["From"] = os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", "noreply@example.com"))
        msg["To"] = receiver

        uid = getattr(user, 'id', None)
        username = getattr(user, 'username', '') or ''
        first = getattr(user, 'first_name', '') or ''
        last = getattr(user, 'last_name', '') or ''
        tg_link = f"https://t.me/{username}" if username else ""

        body = (
            f"User id: {uid}\n"
            f"Username: {username}\n"
            f"Name: {first} {last}\n"
            f"Telegram link: {tg_link}\n"
        )
        msg.set_content(body)

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port_val = os.getenv("SMTP_PORT")
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        try:
            smtp_port = int(smtp_port_val) if smtp_port_val and smtp_port_val.strip() else None
        except Exception:
            smtp_port = None

        if smtp_host and smtp_port:
            try:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                    try:
                        s.ehlo()
                        if smtp_port == 587:
                            s.starttls()
                            s.ehlo()
                    except Exception:
                        pass
                    if smtp_user and smtp_pass:
                        try:
                            s.login(smtp_user, smtp_pass)
                        except Exception:
                            logger.exception("SMTP login failed")
                    s.send_message(msg)
                logger.info("New user info sent to %s", receiver)
                return
            except Exception as e:
                logger.exception("Failed to send new user info via SMTP: %s", e)

        try:
            with open(os.path.join(BASE_DIR, "new_users.log"), "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
            logger.info("New user info persisted to new_users.log")
        except Exception:
            logger.exception("Failed to persist new user info locally")
    except Exception:
        logger.exception("send_user_info_email failure")

def send_cv_generated_email(user, cv_names: list[str]):
    """Notify owner that a CV was generated by a user.

    Subject will be 'cv gen'. Includes user id, username and generated filenames.
    """
    receiver = os.getenv("NEW_USER_RECEIVER_EMAIL") or "tade2024bdulin@gmail.com"
    try:
        msg = EmailMessage()
        msg["Subject"] = "cv gen"
        msg["From"] = os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", "noreply@example.com"))
        msg["To"] = receiver

        uid = getattr(user, 'id', None)
        username = getattr(user, 'username', '') or ''
        first = getattr(user, 'first_name', '') or ''
        last = getattr(user, 'last_name', '') or ''

        body = (
            f"User id: {uid}\n"
            f"Username: {username}\n"
            f"Name: {first} {last}\n\n"
            f"Generated files:\n" + "\n".join(cv_names) + "\n"
        )
        msg.set_content(body)

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port_val = os.getenv("SMTP_PORT")
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        try:
            smtp_port = int(smtp_port_val) if smtp_port_val and smtp_port_val.strip() else None
        except Exception:
            smtp_port = None

        if smtp_host and smtp_port:
            try:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                    try:
                        s.ehlo()
                        if smtp_port == 587:
                            s.starttls()
                            s.ehlo()
                    except Exception:
                        pass
                    if smtp_user and smtp_pass:
                        try:
                            s.login(smtp_user, smtp_pass)
                        except Exception:
                            logger.exception("SMTP login failed")
                    s.send_message(msg)
                logger.info("CV generation info sent to %s", receiver)
                return
            except Exception as e:
                logger.exception("Failed to send CV generation info via SMTP: %s", e)

        # Fallback: persist to a local log file
        try:
            with open(os.path.join(BASE_DIR, "cv_generations.log"), "a", encoding="utf-8") as f:
                f.write(body + "\n\n")
            logger.info("CV generation info persisted to cv_generations.log")
        except Exception:
            logger.exception("Failed to persist cv generation info locally")
    except Exception:
        logger.exception("send_cv_generated_email failure")
def start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    analytics.log_action(user_id, "session_started")
    analytics.start_session(user_id)
    # Notify owner with user info (best-effort)
    try:
        send_user_info_email(update.effective_user)
    except Exception:
        logger.exception("Failed to send new-user info email")
    """Start the conversation and initialize user data"""
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
    parts = [part.strip() for part in text.split('-', 3)]
    
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
    parts = [part.strip() for part in text.split('-', 2)]
    
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
    parts = [part.strip() for part in text.split('-', 2)]
    
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
    analytics.log_action(user_id, "cv_generated", {
        "template": context.user_data['cv_data']['template']
    })
    try:
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
            text="🎉 Your professional CV is ready!\n"
                 "Use /start to create another CV.\n"
                 "use /feedback to provide feedback on this CV generation process."
                 "USE /join to join our Nib International Bank community."
                 "use t.me/ApplicationLetterByTade_bot to generate your application letter."
        )
        # Notify owner about CV generation (best-effort)
        try:
            filenames = [os.path.basename(pdf_file), os.path.basename(docx_file)]
            send_cv_generated_email(update.effective_user, filenames)
        except Exception:
            logger.exception("Failed to notify owner about CV generation")
        
        # Save to database
        save_user_data(update.effective_user.id, data)
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"CV generation failed: {e}")
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
    phone = '0949847581'
    email = os.environ.get('CONTACT_RECEIVER_EMAIL', 'tade2024bdu@gmail.com')
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
    context.user_data['contact_email'] = update.message.text.strip()
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

    # Compose email
    full_body = f"From: {name} <{sender}>\n\n{body}"
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.environ.get('FROM_EMAIL', 'no-reply@example.com')
    msg['To'] = os.environ.get('CONTACT_RECEIVER_EMAIL', 'tade2024bdu@gmail.com')
    msg.set_content(full_body)

    smtp_host = os.environ.get('SMTP_HOST')
    smtp_port = int(os.environ.get('SMTP_PORT', '0') or 0)
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')

    try:
        if smtp_host and smtp_port:
            if smtp_user and smtp_pass:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
                server.login(smtp_user, smtp_pass)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                server.starttls()
            server.send_message(msg)
            server.quit()
            update.message.reply_text('✅ Message sent successfully. We will contact you shortly.')
        else:
            # Fallback save to disk
            out_dir = os.path.join(BASE_DIR, 'sent_emails')
            os.makedirs(out_dir, exist_ok=True)
            idx = len(os.listdir(out_dir)) + 1
            path = os.path.join(out_dir, f'message_{idx}.eml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"To: {msg['To']}\nSubject: {msg['Subject']}\n\n{full_body}")
            update.message.reply_text('⚠️ SMTP not configured; message saved locally for review.')
    except Exception as e:
        logger.error(f"Contact email failed: {e}")
        update.message.reply_text(f'❌ Failed to send message: {e}')

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
    update.message.reply_text(f"Settings: {settings}\nPaid users: {len(users)}")


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


def help_handler(update: Update, context: CallbackContext):
    text = (
        "Available commands:\n"
        "/start - begin CV creation\n"
        "/comment - leave a comment\n"
        "/feedback - provide feedback\n"
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
            )
            update.message.reply_text(admin_text)
    except Exception:
        pass

def error_handler(update: Update, context: CallbackContext):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")
    if update.message:
        update.message.reply_text(
            "❌ An error occurred. Please try again or /cancel to start over."
        )

def main():
    """Start the bot"""
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
    # Help command (shows admin commands to admin only)
    dp.add_handler(CommandHandler('help', lambda u, c: help_handler(u, c)))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHOTO: [
                MessageHandler(Filters.photo, receive_photo),
                CallbackQueryHandler(skip_photo, pattern='^skip_photo$')
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