from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CallbackContext,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler
)
from user_analytics import analytics

# Feedback conversation states
FEEDBACK_RATING, FEEDBACK_COMMENTS = range(2)

def feedback_command(update: Update, context: CallbackContext):
    """Entry point for feedback collection"""
    analytics.log_action(update, "feedback_initiated")
    
    keyboard = [
        [InlineKeyboardButton("⭐ Rate 1", callback_data='rate_1'),
         InlineKeyboardButton("⭐⭐ Rate 2", callback_data='rate_2'),
         InlineKeyboardButton("⭐⭐⭐ Rate 3", callback_data='rate_3')],
        [InlineKeyboardButton("⭐⭐⭐⭐ Rate 4", callback_data='rate_4'),
         InlineKeyboardButton("⭐⭐⭐⭐⭐ Rate 5", callback_data='rate_5')],
        [InlineKeyboardButton("✏️ Text Feedback", callback_data='text_feedback')]
    ]
    
    update.message.reply_text(
        "📝 Please provide your feedback:\n\n"
        "1. Rate your experience (1-5 stars)\n"
        "2. Or send text feedback directly",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return FEEDBACK_RATING

def handle_rating(update: Update, context: CallbackContext):
    """Handle rating selection from buttons"""
    query = update.callback_query
    query.answer()
    
    if query.data.startswith('rate_'):
        rating = int(query.data.split('_')[1])
        context.user_data['feedback_rating'] = rating
        analytics.record_feedback(update, rating=rating)
        
        query.edit_message_text(
            f"Thanks for your {rating} star rating! "
            "Would you like to add any comments? (or /skip to finish)"
        )
        return FEEDBACK_COMMENTS
    elif query.data == 'text_feedback':
        query.edit_message_text(
            "Please type your feedback message:"
        )
        return FEEDBACK_COMMENTS

def handle_feedback_text(update: Update, context: CallbackContext):
    """Handle text feedback submission"""
    comments = update.message.text
    rating = context.user_data.get('feedback_rating')
    
    analytics.record_feedback(
        update,
        rating=rating,
        comments=comments
    )
    
    update.message.reply_text(
        "✅ Thank you for your valuable feedback!\n"
        "We appreciate your time and will use this to improve our service."
    )
    
    if 'feedback_rating' in context.user_data:
        del context.user_data['feedback_rating']
    
    return ConversationHandler.END

def skip_comments(update: Update, context: CallbackContext):
    """Handle skipping comments after rating"""
    analytics.log_action(update, "feedback_completed_without_comments")
    update.message.reply_text("Thank you for your rating!")
    
    if 'feedback_rating' in context.user_data:
        del context.user_data['feedback_rating']
    
    return ConversationHandler.END

def cancel_feedback(update: Update, context: CallbackContext):
    """Cancel feedback collection"""
    analytics.log_action(update, "feedback_cancelled")
    update.message.reply_text("Feedback collection cancelled.")
    
    if 'feedback_rating' in context.user_data:
        del context.user_data['feedback_rating']
    
    return ConversationHandler.END

# Create feedback conversation handler
feedback_conversation = ConversationHandler(
    entry_points=[CommandHandler('feedback', feedback_command)],
    states={
        FEEDBACK_RATING: [CallbackQueryHandler(handle_rating)],
        FEEDBACK_COMMENTS: [
            MessageHandler(Filters.text & ~Filters.command, handle_feedback_text),
            CommandHandler('skip', skip_comments)
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_feedback)]
)