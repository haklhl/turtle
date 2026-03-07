import unittest

from sea_turtle.channels.telegram import (
    _split_telegram_chunks,
    markdown_to_telegram_html,
)


class TelegramFormattingTests(unittest.TestCase):
    def test_converts_bold_inline_code_and_fence(self):
        text = "**bold** and `code`\n```python\nprint('x')\n```"
        html = markdown_to_telegram_html(text)
        self.assertIn("<b>bold</b>", html)
        self.assertIn("<code>code</code>", html)
        self.assertIn("<pre><code>print", html)

    def test_converts_link_italic_strike_underline_and_spoiler(self):
        text = (
            "[OpenAI](https://openai.com) "
            "*italic* "
            "~~strike~~ __under__ ||secret||"
        )
        html = markdown_to_telegram_html(text)
        self.assertIn('<a href="https://openai.com">OpenAI</a>', html)
        self.assertIn("<i>italic</i>", html)
        self.assertIn("<s>strike</s>", html)
        self.assertIn("<u>under</u>", html)
        self.assertIn("<tg-spoiler>secret</tg-spoiler>", html)

    def test_preserves_plain_underscores(self):
        text = "snake_case and _literal_ and foo_bar"
        html = markdown_to_telegram_html(text)
        self.assertEqual(html, "snake_case and _literal_ and foo_bar")

    def test_converts_blockquote(self):
        text = "> quoted\n> still quoted\nplain"
        html = markdown_to_telegram_html(text)
        self.assertIn("<blockquote>quoted\nstill quoted</blockquote>", html)
        self.assertTrue(html.endswith("plain"))

    def test_escapes_plain_html(self):
        text = "<b>not trusted</b>"
        html = markdown_to_telegram_html(text)
        self.assertEqual(html, "&lt;b&gt;not trusted&lt;/b&gt;")

    def test_chunker_preserves_reasonable_limits(self):
        text = ("a" * 2000) + "\n\n" + ("b" * 2000)
        chunks = _split_telegram_chunks(text, limit=3500)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
