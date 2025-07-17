import os
import logging
from typing import Dict, Optional
from telegram import Update
from telegram.ext import CallbackContext
import filetype  # Modern alternative to imghdr

logger = logging.getLogger(__name__)

class PhotoHandler:
    def __init__(self, temp_dir: str = "temp"):
        self.temp_dir = temp_dir
        os.makedirs(self.temp_dir, exist_ok=True)
        self.allowed_types = ['image/jpeg', 'image/png', 'image/webp']
        self.max_size_mb = 5

    def handle_photo(self, update: Update, context: CallbackContext) -> Dict:
        """Process incoming photo with validation and return file path"""
        try:
            # Get the highest quality photo available
            photo_file = update.message.photo[-1].get_file()
            user_id = update.effective_user.id
            
            # Check file size before downloading
            if photo_file.file_size > self.max_size_mb * 1024 * 1024:
                return {
                    'success': False,
                    'message': f"❌ Image too large (max {self.max_size_mb}MB). Please send a smaller photo."
                }

            # Generate unique filename
            file_path = os.path.join(self.temp_dir, f"photo_{user_id}_{int(photo_file.file_unique_id, 16)}.jpg")
            
            # Download the file
            photo_file.download(file_path)
            
            # Validate the image using filetype
            kind = filetype.guess(file_path)
            if kind is None or kind.mime not in self.allowed_types:
                os.remove(file_path)
                return {
                    'success': False,
                    'message': "❌ Invalid image format. Please send JPEG or PNG."
                }

            logger.info(f"Photo successfully saved to {file_path}")
            return {
                'success': True,
                'file_path': file_path,
                'message': "✅ Professional photo received! Now, what's your full name?"
            }

        except Exception as e:
            logger.error(f"Error handling photo: {str(e)}", exc_info=True)
            # Clean up if file was partially downloaded
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)
            return {
                'success': False,
                'message': "❌ Failed to process photo. Please try again or type /skip to skip."
            }

    def cleanup_photo(self, file_path: str) -> bool:
        """Safely remove temporary photo file with validation"""
        try:
            if file_path and os.path.exists(file_path):
                # Verify path is within our temp directory for security
                if os.path.abspath(file_path).startswith(os.path.abspath(self.temp_dir)):
                    os.remove(file_path)
                    logger.info(f"Cleaned up photo: {file_path}")
                    return True
                logger.warning(f"Attempted to clean up file outside temp dir: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up photo: {str(e)}", exc_info=True)
        return False

    def get_user_photo_path(self, user_id: int) -> Optional[str]:
        """Find existing photo for user if it exists"""
        try:
            for filename in os.listdir(self.temp_dir):
                if filename.startswith(f"photo_{user_id}_"):
                    file_path = os.path.join(self.temp_dir, filename)
                    if os.path.exists(file_path):
                        return file_path
        except Exception as e:
            logger.error(f"Error finding user photo: {str(e)}")
        return None