import asyncio
import unittest
from unittest.mock import patch

from sea_turtle.llm.codex import CodexProvider


class CodexProviderTests(unittest.TestCase):
    def test_timeout_raises_user_friendly_message(self):
        class SlowProcess:
            returncode = 0

            async def communicate(self):
                await asyncio.sleep(1)
                return b"", b""

            def kill(self):
                return None

        provider = CodexProvider(command="codex", timeout_seconds=0)

        async def fake_exec(*args, **kwargs):
            return SlowProcess()

        async def run():
            with patch("asyncio.create_subprocess_exec", fake_exec):
                await provider._run_codex_command(
                    prompt="hi",
                    model="codex-5.4",
                    output_file="/tmp/out.txt",
                )

        with self.assertRaisesRegex(RuntimeError, "命令超时"):
            asyncio.run(run())

    def test_model_alias_maps_to_cli_model(self):
        provider = CodexProvider(command="codex")
        command = provider._build_command(
            prompt="hi",
            model="codex-spark",
            output_file="/tmp/out.txt",
        )
        self.assertIn("gpt-5.3-codex-spark", command)

    def test_image_paths_are_forwarded_to_codex_cli(self):
        provider = CodexProvider(command="codex")
        command = provider._build_command(
            prompt="inspect image",
            model="codex-5.4",
            output_file="/tmp/out.txt",
            image_paths=["/tmp/a.png", "/tmp/b.jpg"],
        )
        self.assertEqual(command.count("--image"), 2)
        self.assertIn("/tmp/a.png", command)
        self.assertIn("/tmp/b.jpg", command)
        self.assertIn("--ephemeral", command)

    def test_prompt_includes_attachment_transcript(self):
        provider = CodexProvider(command="codex")
        prompt = provider._build_prompt(
            [
                {"role": "user", "content": "look", "attachments": ["/tmp/demo.png"]},
            ],
            tools=None,
        )
        self.assertIn("ATTACHMENTS: /tmp/demo.png", prompt)

    def test_command_is_stateless(self):
        provider = CodexProvider(
            command="codex",
            sandbox="danger-full-access",
            workdir="/tmp/work",
            reasoning_effort="high",
        )
        command = provider._build_command(
            prompt="hello again",
            model="codex-5.4",
            output_file="/tmp/out.txt",
            image_paths=["/tmp/demo.png"],
        )
        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--cd", command)
        self.assertIn("/tmp/work", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("--image", command)
        self.assertIn("--sandbox", command)
        self.assertIn("--ephemeral", command)


if __name__ == "__main__":
    unittest.main()
