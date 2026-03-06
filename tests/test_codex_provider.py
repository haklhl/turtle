import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sea_turtle.llm.codex import CodexProvider


class FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self._stdout = stdout.encode()
        self._stderr = stderr.encode()
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr


class CodexProviderTests(unittest.TestCase):
    def test_session_cache_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "reply.txt"
            session_file = Path(tmpdir) / ".codex_sessions.json"
            output_path.write_text("done", encoding="utf-8")

            provider = CodexProvider(
                command="codex",
                workdir=tmpdir,
                persist_sessions=True,
                session_file=str(session_file),
            )

            async def fake_exec(*cmd, **kwargs):
                output_index = cmd.index("--output-last-message") + 1
                Path(cmd[output_index]).write_text("done", encoding="utf-8")
                return FakeProcess(stdout=json.dumps({
                    "type": "session_meta",
                    "payload": {"id": "sess-123"},
                }))

            async def run():
                with patch("asyncio.create_subprocess_exec", fake_exec):
                    return await provider.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        model="codex-oss",
                        metadata={"conversation_id": "telegram|chat:1|user:2"},
                    )

            response = asyncio.run(run())
            self.assertEqual(response.content, "done")
            self.assertTrue(session_file.exists())
            data = json.loads(session_file.read_text(encoding="utf-8"))
            self.assertEqual(data["telegram|chat:1|user:2"], "sess-123")

    def test_session_cache_loads_from_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_file = Path(tmpdir) / ".codex_sessions.json"
            session_file.write_text(json.dumps({"conv": "sess-abc"}), encoding="utf-8")
            provider = CodexProvider(
                command="codex",
                workdir=tmpdir,
                persist_sessions=True,
                session_file=str(session_file),
            )
            self.assertEqual(provider._session_cache["conv"], "sess-abc")

    def test_model_alias_maps_to_cli_model(self):
        provider = CodexProvider(command="codex")
        command = provider._build_command(
            prompt="hi",
            model="codex-spark",
            output_file="/tmp/out.txt",
            session_id=None,
        )
        self.assertIn("gpt-5.3-codex-spark", command)


if __name__ == "__main__":
    unittest.main()
