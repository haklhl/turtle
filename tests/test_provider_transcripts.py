import json
import sys
import types
import unittest


class ProviderTranscriptTests(unittest.TestCase):
    def setUp(self):
        self._added_modules = set()

    def tearDown(self):
        for name in self._added_modules:
            sys.modules.pop(name, None)

    def _register_module(self, name, module):
        sys.modules[name] = module
        self._added_modules.add(name)

    def _install_openai_stub(self):
        module = types.ModuleType("openai")

        class AsyncOpenAI:
            def __init__(self, *args, **kwargs):
                pass

        module.AsyncOpenAI = AsyncOpenAI
        self._register_module("openai", module)

    def _install_anthropic_stub(self):
        module = types.ModuleType("anthropic")

        class AsyncAnthropic:
            def __init__(self, *args, **kwargs):
                pass

        module.AsyncAnthropic = AsyncAnthropic
        self._register_module("anthropic", module)

    def _install_google_stub(self):
        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        types_module = types.ModuleType("google.genai.types")

        class Client:
            def __init__(self, *args, **kwargs):
                self.aio = types.SimpleNamespace(models=None)

        class Content:
            def __init__(self, role, parts):
                self.role = role
                self.parts = parts

        class Tool:
            def __init__(self, function_declarations):
                self.function_declarations = function_declarations

        class FunctionDeclaration:
            def __init__(self, name, description, parameters):
                self.name = name
                self.description = description
                self.parameters = parameters

        class GenerateContentConfig:
            def __init__(self, temperature, max_output_tokens):
                self.temperature = temperature
                self.max_output_tokens = max_output_tokens
                self.system_instruction = None
                self.tools = None

        class Part:
            @staticmethod
            def from_text(text):
                return {"kind": "text", "text": text}

            @staticmethod
            def from_function_call(name, args):
                return {"kind": "function_call", "name": name, "args": args}

            @staticmethod
            def from_function_response(name, response):
                return {"kind": "function_response", "name": name, "response": response}

        genai_module.Client = Client
        types_module.Content = Content
        types_module.Tool = Tool
        types_module.FunctionDeclaration = FunctionDeclaration
        types_module.GenerateContentConfig = GenerateContentConfig
        types_module.Part = Part
        genai_module.types = types_module

        google_module.genai = genai_module
        self._register_module("google", google_module)
        self._register_module("google.genai", genai_module)
        self._register_module("google.genai.types", types_module)

    def test_openai_transcript_keeps_tool_call_ids(self):
        self._install_openai_stub()
        from sea_turtle.llm.openai import OpenAIProvider

        provider = OpenAIProvider(api_key="x")
        converted = provider._convert_messages([
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-1", "name": "execute_shell", "arguments": {"command": "pwd"}}],
            },
            {"role": "tool", "content": "stdout:\n/home", "name": "execute_shell", "tool_call_id": "call-1"},
        ])

        self.assertEqual(converted[1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(converted[2]["tool_call_id"], "call-1")

    def test_anthropic_transcript_keeps_tool_call_ids(self):
        self._install_anthropic_stub()
        from sea_turtle.llm.anthropic import AnthropicProvider

        provider = AnthropicProvider(api_key="x")
        system_prompt, conversation = provider._extract_messages([
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-2", "name": "read_tasks", "arguments": {}}],
            },
            {"role": "tool", "content": "(no tasks)", "name": "read_tasks", "tool_call_id": "call-2"},
        ])

        self.assertEqual(system_prompt, "sys")
        self.assertEqual(conversation[0]["content"][0]["id"], "call-2")
        self.assertEqual(conversation[1]["content"][0]["tool_use_id"], "call-2")

    def test_google_transcript_keeps_function_call_history(self):
        self._install_google_stub()
        from sea_turtle.llm.google import GoogleProvider

        provider = GoogleProvider(api_key="x")
        _, contents = provider._convert_messages([
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "execute_shell", "arguments": {"command": "pwd"}}],
            },
            {"role": "tool", "content": "/tmp", "name": "execute_shell"},
        ])

        self.assertEqual(contents[0].parts[0]["kind"], "function_call")
        self.assertEqual(contents[1].parts[0]["kind"], "function_response")


if __name__ == "__main__":
    unittest.main()
