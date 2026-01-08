from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, CallbackContext

def join_community(update: Update, context: CallbackContext):
    """Send social media links + confirmation button."""
    keyboard = [
        # Nib International Bank official pages
        [InlineKeyboardButton("📢 Join Nib Telegram", url="https://t.me/nibinternationalbanksc")],
        [InlineKeyboardButton("🎵 Follow Nib TikTok", url="https://www.tiktok.com/@nibinternationalbank")],
        # User / personal pages
        [InlineKeyboardButton("▶️ Subscribe YouTube (EagleTube)", url="https://www.youtube.com/@EagleTube-ph6wh")],
        [InlineKeyboardButton("🎵 Follow TikTok (coming_to_hacker)", url="https://www.tiktok.com/@coming_to_hacker")],
        [InlineKeyboardButton("✅ I've Joined", callback_data="joined_success")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    update.message.reply_text(
        "🌐 Join the official Nib International Bank pages or my personal socials below and confirm after joining:",
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
