import os
import logging
from typing import Dict
from telegram import Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)

class PhotoHandler:
    def __init__(self, temp_dir: str = "temp"):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)

    def handle_photo(self, update: Update, context: CallbackContext) -> Dict:
        """Process incoming photo and return file path"""
        try:
            photo_file = update.message.photo[-1].get_file()
            user_id = update.effective_user.id
            file_path = os.path.join(self.temp_dir, f"photo_{user_id}.jpg")
            
            photo_file.download(file_path)
            logger.info(f"Photo saved to {file_path}")
            
            return {
                'success': True,
                'file_path': file_path,
                'message': "✅ Photo received. Now, what's your full name?"
            }
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            return {
                'success': False,
                'message': "❌ Failed to process photo. Please try again or type /skip to skip."
            }

    def cleanup_photo(self, file_path: str) -> bool:
        """Remove temporary photo file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
        except Exception as e:
            logger.error(f"Error cleaning up photo: {e}")
        return False
