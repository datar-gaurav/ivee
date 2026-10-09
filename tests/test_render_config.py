import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server" / "scripts"))
import render_config as rc  # noqa: E402

GOOD = {"GROQ_API_KEY": "gsk_test", "SERVER_LAN_IP": "192.168.1.20"}


class RenderConfigTest(unittest.TestCase):
    def test_example_env_needs_only_key(self):
        env = rc.load_env(REPO / "server" / "ivee.env.example")
        self.assertEqual(rc.problems(env), ["GROQ_API_KEY is still the example value"])
        self.assertEqual(rc.problems({**env, "GROQ_API_KEY": "gsk_real"}), [])

    def test_renders_every_placeholder(self):
        out = rc.render(GOOD, "You are Speel.")
        self.assertNotIn("{{", out)
        self.assertIn("ws://192.168.1.20:8000/xiaozhi/v1/", out)
        self.assertIn("  You are Speel.", out)
        self.assertIn("timezone_offset: 0", out)

    def test_extra_provider_only_when_set(self):
        out = rc.render(GOOD, "x")
        self.assertIn("extra_model: none", out)
        self.assertIn("extra_base_url: none", out)
        out = rc.render({**GOOD, "EXTRA_LLM_BASE_URL": "https://example.com/v1", "EXTRA_LLM_MODEL": "m-1"}, "x")
        self.assertIn("extra_model: m-1", out)
        self.assertIn("extra_base_url: https://example.com/v1", out)
        self.assertIn("extra_api_key: none", out)

    def test_problems(self):
        cases = {
            "SERVER_LAN_IP is not set": {"SERVER_LAN_IP": ""},
            "LLM_MODULE must be one of GroqLLM, OllamaLLM": {"LLM_MODULE": "Nope"},
            "LLM_MODULE=OllamaLLM needs an OLLAMA_MODEL": {"LLM_MODULE": "OllamaLLM"},
            "SERVER_WS_PORT must be a port number": {"SERVER_WS_PORT": "99999"},
            "TIMEZONE_OFFSET must be hours from UTC, like -7 or 5.5": {"TIMEZONE_OFFSET": "PST"},
            "TTS_PITCH must be a number from 0.8 to 1.5": {"TTS_PITCH": "3"},
            "TTS_VOICE must be a Kokoro voice name, like af_heart": {"TTS_VOICE": "../x"},
            "SERVER_LAN_IP must be an IP address or host name": {"SERVER_LAN_IP": "1.2.3.4 #x"},
            "EXTRA_LLM_BASE_URL must be an http(s) URL, like https://integrate.api.nvidia.com/v1":
                {"EXTRA_LLM_MODEL": "m"},
            "EXTRA_LLM_MODEL must be set to a model name": {"EXTRA_LLM_BASE_URL": "https://x.io/v1"},
            "GROQ_LLM_MODEL doesn't look like a model name": {"GROQ_LLM_MODEL": "a b"},
        }
        for message, change in cases.items():
            with self.subTest(message):
                self.assertIn(message, rc.problems({**GOOD, **change}))
        with self.assertRaises(rc.ConfigError):
            rc.render({**GOOD, "TTS_PITCH": "3"}, "x")

    def test_identity_next_to_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "ivee.env"
            env.write_text("GROQ_API_KEY=gsk_test\nSERVER_LAN_IP=10.0.0.2\n")
            self.assertEqual(rc.identity_path(env), rc.IDENTITY)
            (Path(tmp) / "identity.md").write_text("You are Robo.")
            out_dir = Path(tmp) / "data"
            config = rc.write(env, out_dir)
            self.assertIn("You are Robo.", config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertTrue((out_dir / "ivee-base-prompt.txt").exists())


if __name__ == "__main__":
    unittest.main()
