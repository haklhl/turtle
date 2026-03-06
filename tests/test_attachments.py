import unittest

from sea_turtle.channels.telegram import ALLOWED_DOCUMENT_SUFFIXES
from sea_turtle.core.agent_worker import IMAGE_ATTACHMENT_SUFFIXES


class AttachmentTests(unittest.TestCase):
    def test_image_attachment_suffixes_cover_supported_telegram_images(self):
        self.assertIn(".png", IMAGE_ATTACHMENT_SUFFIXES)
        self.assertIn(".jpg", IMAGE_ATTACHMENT_SUFFIXES)
        self.assertIn(".jpeg", IMAGE_ATTACHMENT_SUFFIXES)
        self.assertNotIn(".pdf", IMAGE_ATTACHMENT_SUFFIXES)

    def test_allowed_document_suffixes_cover_common_safe_types(self):
        self.assertIn(".pdf", ALLOWED_DOCUMENT_SUFFIXES)
        self.assertIn(".txt", ALLOWED_DOCUMENT_SUFFIXES)
        self.assertIn(".py", ALLOWED_DOCUMENT_SUFFIXES)
        self.assertNotIn(".exe", ALLOWED_DOCUMENT_SUFFIXES)


if __name__ == "__main__":
    unittest.main()
