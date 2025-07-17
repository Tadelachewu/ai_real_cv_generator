import os
import logging
import uuid
import sqlite3
from typing import Dict, Optional
from datetime import datetime

import requests
from telegram import (
    Update, InputFile,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    ConversationHandler, CallbackContext, CallbackQueryHandler
)
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from weasyprint import  CSS

from dotenv import load_dotenv
from docx import Document
# Add at the top of your main bot file
from photo_handler import PhotoHandler

# Initialize the photo handler at startup
photo_handler = PhotoHandler()

# Load environment variables
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Logging setup
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)

# States
(
    NAME, EMAIL, PHONE, SUMMARY, EXPERIENCE, EDUCATION,
    SKILLS, LANGUAGES, PROJECTS, REVIEW, WAITING_CALLBACK, PHOTO
) = range(12)

# DB setup
def init_db():
    conn = sqlite3.connect("cv_bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        data TEXT,
        last_updated TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# Gemini API call
def ask_gemini(prompt: str) -> str:
    """Modified to ensure only clean CV content is returned"""
    clean_prompt = f"""
    You are a professional CV writer. 
    Return only the requested CV content with:
    - No explanations
    - No instructions
    - No additional text outside the requested content
    - No markdown formatting
    - No placeholders
    
    {prompt}
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{
            "parts": [{
                "text": clean_prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.3,  # Less creative, more factual
            "topP": 0.8,
            "stopSequences": ["Explanation:", "Note:", "Here's"]
        }
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        response.raise_for_status()
        text = response.json()['candidates'][0]['content']['parts'][0]['text']
        
        # Post-processing cleanup
        text = text.replace("Here's the enhanced version:", "")
        text = text.replace("**", "")  # Remove markdown bold
        text = text.replace("*", "")   # Remove markdown italics
        text = text.split("Note:")[0]  # Remove any notes
        return text.strip()
    except Exception as e:
        logger.error(f"Gemini API Error: {e}")
        return ""
# Helpers for DB
def save_user_data(user_id: int, data: Dict):
    conn = sqlite3.connect("cv_bot.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?)",
              (user_id, str(data), datetime.now().isoformat()))
    conn.commit()
    conn.close()

def load_user_data(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect("cv_bot.db")
    c = conn.cursor()
    c.execute("SELECT data FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return eval(row[0])
        except Exception as e:
            logger.error(f"Error evaluating user data: {e}")
            return None
    return None

# PDF and DOCX generators (same as before)
# ... (keep all previous imports and setup code)

def generate_pdf(data: Dict) -> str:
    """Generate clean, production-ready PDF"""
    enhanced_data = enhance_with_ai(data.copy())
    
    # Final cleanup pass
    enhanced_data['summary'] = enhanced_data.get('summary', '').split("Note:")[0].strip()
    
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template("professional_cv.html")
    
    enhanced_data.update({
        'current_date': datetime.now().strftime("%B %Y"),
        'linkedin': enhanced_data.get('linkedin', ''),
        'portfolio': enhanced_data.get('portfolio', '')
    })
    
    # Clean photo handling
    photo_path = data.get("photo_path")
    if photo_path and os.path.exists(photo_path):
        enhanced_data['photo_url'] = f"file://{os.path.abspath(photo_path)}"
    else:
        enhanced_data['photo_url'] = None
        
    html = template.render(enhanced_data)
    
    # Generate PDF with professional styling
    css = CSS(string='''
        body { font-family: 'Arial', sans-serif; }
        .ai-generated-content { display: none; }  /* Hide any AI markers */
    ''')
    
    filename = f"temp/{enhanced_data['name'].replace(' ', '_')}_Professional_CV.pdf"
    HTML(string=html).write_pdf(filename, stylesheets=[css])
    return filename

def enhance_with_ai(data: Dict) -> Dict:
    """Use AI to enhance the CV while ensuring clean, production-ready output"""
    try:
        # Build experience string separately to avoid complex f-string
        experience_str = "\n".join(
            f"{e['role']} at {e['company']} ({e['years']}): {e['description']}"
            for e in data.get('experience', [])
        )
        
        # Build education string separately
        education_str = "\n".join(
            f"{e['degree']} at {e['institution']} ({e['years']})"
            for e in data.get('education', [])
        )
        
        # Build prompt that demands clean output
        prompt = f"""
        Create a professional CV using only this information. 
        Return ONLY the final CV content with:
        - No explanations
        - No instructions
        - No placeholders
        - No markdown formatting
        
        Candidate: {data.get('name', '')}
        Contact: {data.get('email', '')} | {data.get('phone', '')}
        
        Professional Summary:
        {data.get('summary', '')}
        
        Experience:
        {experience_str}
        
        Education:
        {education_str}
        
        Skills: {', '.join(data.get('skills', []))}
        Languages: {', '.join(data.get('languages', []))}
        
        Return a JSON-formatted dictionary with these enhanced sections:
        - summary (3-4 sentence professional summary)
        - experience (with quantified achievements)
        - education
        - skills (organized by category)
        - languages
        """
        
        # Rest of the function remains the same...
        
        response = ask_gemini(prompt)
        
        # Parse and clean the response
        try:
            # Find the first { and last } to extract pure JSON
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            json_str = response[json_start:json_end]
            
            enhanced = json.loads(json_str)
            
            # Merge only the professional enhancements
            for key in ['summary', 'experience', 'education', 'skills', 'languages']:
                if key in enhanced:
                    data[key] = enhanced[key]
                    
            # Ensure experience descriptions are clean
            for job in data.get('experience', []):
                if 'description' in job:
                    job['description'] = job['description'].split("Note:")[0].strip()
                    
        except json.JSONDecodeError:
            logger.warning("Couldn't parse JSON response, using original data")
            
    except Exception as e:
        logger.error(f"AI enhancement failed: {e}")
    
    return data
def clean_cv_text(text: str) -> str:
    """Remove any AI artifacts from text"""
    removals = [
        "Here's the enhanced version:",
        "Note:",
        "Important:",
        "Pro Tip:",
        "**",
        "*",
        "```"
    ]
    for removal in removals:
        text = text.replace(removal, "")
    return text.strip()


def generate_docx(data: Dict) -> str:
    """Generate a professional DOCX version of the CV"""
    enhanced_data = enhance_with_ai(data.copy())
    doc = Document()
    
    # Header with contact info
    photo_path = data.get("photo_path")
    if photo_path and os.path.exists(photo_path):
      try:
        doc.add_picture(photo_path, width=Inches(1.5))
      except Exception as e:
        logger.warning(f"Couldn't add profile photo: {e}")
    doc.add_heading(enhanced_data['name'], level=0)
    contact = doc.add_paragraph()
    contact.add_run(f"{enhanced_data.get('email', '')} | {enhanced_data.get('phone', '')}")
    if enhanced_data.get('linkedin'):
        contact.add_run(f" | LinkedIn: {enhanced_data['linkedin']}")
    if enhanced_data.get('portfolio'):
        contact.add_run(f" | Portfolio: {enhanced_data['portfolio']}")
    contact.alignment = 1  # Center alignment
    
    # Summary section
    doc.add_heading('Professional Summary', level=1)
    doc.add_paragraph(enhanced_data.get('summary', ''))
    
    # Professional Experience
    if enhanced_data.get('experience'):
        doc.add_heading('Professional Experience', level=1)
        for job in enhanced_data['experience']:
            # Job title and company
            p = doc.add_paragraph()
            p.add_run(f"{job['role']}").bold = True
            p.add_run(f" at {job['company']} | {job['years']}")
            
            # Job description
            doc.add_paragraph(job['description'], style='List Bullet')
            
            # Key achievements if available
            if job.get('achievements'):
                doc.add_paragraph("Key Achievements:", style='List Bullet')
                for achievement in job['achievements']:
                    doc.add_paragraph(achievement, style='List Bullet 2')
    
    # Education
    if enhanced_data.get('education'):
        doc.add_heading('Education', level=1)
        for edu in enhanced_data['education']:
            p = doc.add_paragraph()
            p.add_run(f"{edu['degree']}").bold = True
            p.add_run(f" | {edu['institution']} | {edu['years']}")
    
    # Skills with categorization
    if enhanced_data.get('skills'):
        doc.add_heading('Technical Skills', level=1)
        skills_text = ', '.join(enhanced_data['skills'])
        doc.add_paragraph(skills_text)
    
    # Projects
    if enhanced_data.get('projects'):
        doc.add_heading('Key Projects', level=1)
        for project in enhanced_data['projects']:
            p = doc.add_paragraph()
            p.add_run(f"{project['name']}").bold = True
            p.add_run(f" | Technologies: {project['technologies']}")
            doc.add_paragraph(project['description'], style='List Bullet')
    
    # Certifications if available
    if enhanced_data.get('certifications'):
        doc.add_heading('Certifications', level=1)
        for cert in enhanced_data['certifications']:
            doc.add_paragraph(cert, style='List Bullet')
    
    filename = f"temp/{enhanced_data['name'].replace(' ', '_')}_Professional_CV.docx"
    doc.save(filename)
    return filename

# ... (keep all remaining code the same)

# Start command handler
# Modify the receive_photo function
def receive_photo(update: Update, context: CallbackContext) -> int:
    result = photo_handler.handle_photo(update, context)
    if result['success']:
        context.user_data['cv_data']['photo_path'] = result['file_path']
        update.message.reply_text(result['message'])
        return NAME
    else:
        update.message.reply_text(result['message'])
        return PHOTO  # Stay in photo state to retry

# --- ADD SKIP PHOTO HANDLER --- #
def skip_photo(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("👍 No problem! What's your full name?")
    return NAME

def start(update: Update, context: CallbackContext) -> int:
    logger.info(f"User {update.effective_user.id} started.")
    context.user_data['cv_data'] = {
        "name": "", "email": "", "phone": "", "summary": "",
        "experience": [], "education": [],
        "skills": [], "languages": [], "projects": [],
        "photo_path": ""
    }
    update.message.reply_text("📸 Please upload a professional profile photo (or type /skip to skip).")
    return PHOTO

def get_name(update: Update, context: CallbackContext) -> int:
    context.user_data['cv_data']['name'] = update.message.text.strip()
    update.message.reply_text("📧 Your email address?")
    return EMAIL

def get_email(update: Update, context: CallbackContext) -> int:
    context.user_data['cv_data']['email'] = update.message.text.strip()
    update.message.reply_text("📱 Your phone number?")
    return PHONE

def get_phone(update: Update, context: CallbackContext) -> int:
    context.user_data['cv_data']['phone'] = update.message.text.strip()
    update.message.reply_text("🧠 Give me a short summary of your professional profile:")
    return SUMMARY

def get_summary(update: Update, context: CallbackContext) -> int:
    raw_summary = update.message.text.strip()
    polished_summary = ask_gemini(f"Rewrite this summary professionally:\n{raw_summary}")
    context.user_data['cv_data']['summary'] = polished_summary
    update.message.reply_text("Great! Let's add your job experience.")
    return ask_experience(update, context)

# Experience step with buttons
def ask_experience(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        "💼 Enter job experience in format:\nRole - Company - Years - Description"
    )
    return EXPERIENCE

def get_experience(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    parts = text.split('-', 3)
    if len(parts) != 4:
        update.message.reply_text("❗ Please use format: Role - Company - Years - Description")
        return EXPERIENCE

    description = ask_gemini(f"Polish this job description:\n{parts[3].strip()}")
    job = {
        'role': parts[0].strip(),
        'company': parts[1].strip(),
        'years': parts[2].strip(),
        'description': description
    }
    context.user_data['cv_data']['experience'].append(job)

    keyboard = [
        [InlineKeyboardButton("Add Another Experience", callback_data='add_experience')],
        [InlineKeyboardButton("Finish Experience Section", callback_data='finish_experience')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("✅ Experience added. What next?", reply_markup=reply_markup)
    return WAITING_CALLBACK

# Education step with buttons
def ask_education(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        "🎓 Enter your education in format:\nDegree - Institution - Years"
    )
    return EDUCATION

def get_education(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    parts = text.split('-', 2)
    if len(parts) != 3:
        update.message.reply_text("❗ Format: Degree - Institution - Years")
        return EDUCATION

    edu = {
        'degree': parts[0].strip(),
        'institution': parts[1].strip(),
        'years': parts[2].strip()
    }
    context.user_data['cv_data']['education'].append(edu)

    keyboard = [
        [InlineKeyboardButton("Add Another Education", callback_data='add_education')],
        [InlineKeyboardButton("Finish Education Section", callback_data='finish_education')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("✅ Education added. What next?", reply_markup=reply_markup)
    return WAITING_CALLBACK

# Skills step with buttons
def ask_skills(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("🛠️ List your skills (comma separated):")
    return SKILLS

def get_skills(update: Update, context: CallbackContext) -> int:
    skills = [s.strip() for s in update.message.text.split(',')]
    context.user_data['cv_data']['skills'] = skills

    keyboard = [
        [InlineKeyboardButton("Next: Languages", callback_data='finish_skills')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Skills saved.", reply_markup=reply_markup)
    return WAITING_CALLBACK

# Languages step with buttons
def ask_languages(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("🌐 List the languages you speak (comma separated):")
    return LANGUAGES

def get_languages(update: Update, context: CallbackContext) -> int:
    languages = [l.strip() for l in update.message.text.split(',')]
    context.user_data['cv_data']['languages'] = languages

    keyboard = [
        [InlineKeyboardButton("Next: Projects", callback_data='finish_languages')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Languages saved.", reply_markup=reply_markup)
    return WAITING_CALLBACK

# Projects step with buttons
def ask_projects(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        "🛠️ List your projects in format:\nName - Description - Tech Used"
    )
    return PROJECTS

def get_projects(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip()
    parts = text.split('-', 2)
    if len(parts) != 3:
        update.message.reply_text("❗ Format: Name - Description - Tech Used")
        return PROJECTS

    project = {
        'name': parts[0].strip(),
        'description': parts[1].strip(),
        'technologies': parts[2].strip()
    }
    context.user_data['cv_data']['projects'].append(project)

    keyboard = [
        [InlineKeyboardButton("Add Another Project", callback_data='add_project')],
        [InlineKeyboardButton("Finish Projects Section", callback_data='finish_projects')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("✅ Project added. What next?", reply_markup=reply_markup)
    return WAITING_CALLBACK

# Review step with edit options
def review(update: Update, context: CallbackContext) -> int:
    data = context.user_data['cv_data']
    text = "<b>📝 Review your info:</b>\n\n"
    text += f"<b>👤 Name:</b> {data['name']}\n"
    text += f"<b>📧 Email:</b> {data['email']}\n"
    text += f"<b>📱 Phone:</b> {data['phone']}\n\n"

    text += f"<b>🧠 Summary:</b>\n{data['summary']}\n\n"

    text += "<b>💼 Experience:</b>\n"
    for i, job in enumerate(data['experience'], 1):
        text += f"{i}. {job['role']} at {job['company']} ({job['years']})\n"

    text += "\n<b>🎓 Education:</b>\n"
    for i, edu in enumerate(data['education'], 1):
        text += f"{i}. {edu['degree']} at {edu['institution']} ({edu['years']})\n"

    text += f"\n<b>🛠️ Skills:</b>\n{', '.join(data['skills'])}\n"
    text += f"\n<b>🌐 Languages:</b>\n{', '.join(data['languages'])}\n"

    text += "\n<b>🛠️ Projects:</b>\n"
    for i, p in enumerate(data['projects'], 1):
        text += f"{i}. {p['name']} - {p['description']} ({p['technologies']})\n"

    keyboard = [
        [InlineKeyboardButton("Edit Name", callback_data='edit_name'),
         InlineKeyboardButton("Edit Email", callback_data='edit_email')],
        [InlineKeyboardButton("Edit Phone", callback_data='edit_phone'),
         InlineKeyboardButton("Edit Summary", callback_data='edit_summary')],
        [InlineKeyboardButton("Edit Experience", callback_data='edit_experience')],
        [InlineKeyboardButton("Edit Education", callback_data='edit_education')],
        [InlineKeyboardButton("Edit Skills", callback_data='edit_skills')],
        [InlineKeyboardButton("Edit Languages", callback_data='edit_languages')],
        [InlineKeyboardButton("Edit Projects", callback_data='edit_projects')],
        [InlineKeyboardButton("✅ Confirm & Generate CV", callback_data='generate_cv')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    message = update.message or (update.callback_query.message if update.callback_query else None)

    if message:
        message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)
    else:
        logger.error("No message found to send review text.")
        return ConversationHandler.END

    return WAITING_CALLBACK


# Callback handler for buttons
def callback_handler(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data

    # Map button callbacks to states or actions
    if data == 'add_experience':
        query.edit_message_text("💼 Enter job experience in format:\nRole - Company - Years - Description")
        return EXPERIENCE
    elif data == 'finish_experience':
        query.edit_message_text("🎓 Enter your education in format:\nDegree - Institution - Years")
        return EDUCATION

    elif data == 'add_education':
        query.edit_message_text("🎓 Enter education in format:\nDegree - Institution - Years")
        return EDUCATION
    elif data == 'finish_education':
        query.edit_message_text("🛠️ List your skills (comma separated):")
        return SKILLS

    elif data == 'finish_skills':
        query.edit_message_text("🌐 List the languages you speak (comma separated):")
        return LANGUAGES

    elif data == 'finish_languages':
        query.edit_message_text("🛠️ List your projects in format:\nName - Description - Tech Used")
        return PROJECTS

    elif data == 'add_project':
        query.edit_message_text("🛠️ Enter project in format:\nName - Description - Tech Used")
        return PROJECTS
    elif data == 'finish_projects':
    # Edit inline message instead of sending new
        update.callback_query.edit_message_text("✅ Projects section completed. Generating review...")
        return review(update, context)


    # Editing individual fields
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
        query.edit_message_text("Please enter a short summary of your professional profile:")
        return SUMMARY
    elif data == 'edit_experience':
        # Clear experience and restart input
        context.user_data['cv_data']['experience'] = []
        query.edit_message_text("💼 Let's re-enter your job experiences.\nEnter in format:\nRole - Company - Years - Description")
        return EXPERIENCE
    elif data == 'edit_education':
        context.user_data['cv_data']['education'] = []
        query.edit_message_text("🎓 Let's re-enter your education.\nEnter in format:\nDegree - Institution - Years")
        return EDUCATION
    elif data == 'edit_skills':
        query.edit_message_text("🛠️ List your skills (comma separated):")
        return SKILLS
    elif data == 'edit_languages':
        query.edit_message_text("🌐 List the languages you speak (comma separated):")
        return LANGUAGES
    elif data == 'edit_projects':
        context.user_data['cv_data']['projects'] = []
        query.edit_message_text("🛠️ Let's re-enter your projects.\nEnter in format:\nName - Description - Tech Used")
        return PROJECTS

    elif data == 'generate_cv':
        query.edit_message_text("✅ Generating your CV...")
        return generate_cv(update, context)

    elif data == 'cancel':
        query.edit_message_text("❌ CV creation cancelled.")
        return ConversationHandler.END

    else:
        query.edit_message_text("Unknown command, please try again.")
        return WAITING_CALLBACK

# Generate CV and send files
def generate_cv(update: Update, context: CallbackContext) -> int:
    data = context.user_data['cv_data']
    pdf_file = generate_pdf(data)
    docx_file = generate_docx(data)

    chat_id = update.effective_chat.id

    with open(pdf_file, 'rb') as f_pdf:
        context.bot.send_document(chat_id=chat_id, document=InputFile(f_pdf), filename=os.path.basename(pdf_file))
    with open(docx_file, 'rb') as f_docx:
        context.bot.send_document(chat_id=chat_id, document=InputFile(f_docx), filename=os.path.basename(docx_file))

    # Clean up photo file if it exists
    if 'photo_path' in data and data['photo_path']:
        photo_handler.cleanup_photo(data['photo_path'])

    context.bot.send_message(chat_id=chat_id, text="🚀 Your CV is ready! Use /start to create another.")
    return ConversationHandler.END

# Cancel command handler
def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("❌ CV creation cancelled.")
    return ConversationHandler.END

def main():
    if not os.path.exists("temp"):
        os.makedirs("temp")
    if not os.path.exists("templates"):
        os.makedirs("templates")

    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
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
            PHOTO: [
    MessageHandler(Filters.photo, receive_photo),
    CommandHandler("skip", skip_photo)
],# Not used for now but can be extended
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    dp.add_handler(conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
