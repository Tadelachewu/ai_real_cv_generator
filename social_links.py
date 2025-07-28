from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, CallbackContext

def join_community(update: Update, context: CallbackContext):
    """Send social media links + confirmation button."""
    keyboard = [
        [InlineKeyboardButton("📢 Join Telegram", url="https://t.me/nibinternationalbanksc")],
        [InlineKeyboardButton("🎵 Follow TikTok", url="https://www.tiktok.com/@nibinternationalbank")],
        [InlineKeyboardButton("✅ I've Joined", callback_data="joined_success")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "🌐 Join Nib International Bank's official pages and confirm after joining:",
        reply_markup=reply_markup
    )

def joined_success_callback(update: Update, context: CallbackContext):
    """Handle 'I've Joined' confirmation"""
    query = update.callback_query
    query.answer()
    query.edit_message_text("🎉 Thank you for joining our community!\nStay tuned for more updates and opportunities.")

def social_links_handler():
    """Return both command and callback handlers."""
    return [
        CommandHandler("join", join_community),
        CallbackQueryHandler(joined_success_callback, pattern="^joined_success$")
    ]
