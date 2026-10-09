import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server" / "config_ui"))
import ivee_config as ui  # noqa: E402

PASSWORD = "correct horse"


class ConfigPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / "ivee.env"
        self.env.write_text((REPO / "server" / "ivee.env.example").read_text())
        (Path(self.tmp.name) / "ui-password").write_text(ui.hash_password(PASSWORD))
        self.app = ui.App(self.env, "manual")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1])
        all_headers = {"X-Ivee": "1", "Content-Type": "application/json"}
        if self.cookie:
            all_headers["Cookie"] = self.cookie
        all_headers.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, all_headers)
        response = conn.getresponse()
        data = response.read()
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        conn.close()
        return response.status, data

    def login(self):
        status, _ = self.request("POST", "/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200)

    def test_password_hash(self):
        stored = ui.hash_password("abc12345")
        self.assertTrue(ui.check_password("abc12345", stored))
        self.assertFalse(ui.check_password("abc123456", stored))
        self.assertFalse(ui.check_password("x", "garbage"))

    def test_needs_login(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("POST", "/api/save", {"values": {}})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password": "wrong"})[0], 401)

    def test_refuses_cross_site_posts(self):
        self.login()
        self.assertEqual(self.request("POST", "/api/save", {}, {"X-Ivee": ""})[0], 403)
        self.assertEqual(self.request("POST", "/api/save", {}, {"Origin": "http://evil.example"})[0], 403)

    def test_save_keeps_comments_and_hides_key(self):
        self.login()
        status, data = self.request("POST", "/api/save", {"values": {
            "GROQ_API_KEY": "gsk_secret1234", "SERVER_LAN_IP": "192.168.1.9", "TTS_SPEED": "1.0"}})
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["ok"])
        text = self.env.read_text()
        self.assertIn("GROQ_API_KEY=gsk_secret1234", text)
        self.assertIn("TTS_SPEED=1.0", text)
        self.assertIn("# Required. https://console.groq.com/keys", text)

        status, data = self.request("GET", "/api/state")
        self.assertNotIn(b"gsk_secret1234", data)
        state = json.loads(data)
        self.assertTrue(state["keys"]["GROQ_API_KEY"]["set"])
        self.assertEqual(state["keys"]["GROQ_API_KEY"]["hint"], "…1234")
        self.assertFalse(state["keys"]["EXTRA_LLM_API_KEY"]["set"])
        self.assertEqual(state["problems"], [])

        # An empty key keeps the saved one.
        self.request("POST", "/api/save", {"values": {"GROQ_API_KEY": ""}})
        self.assertIn("GROQ_API_KEY=gsk_secret1234", self.env.read_text())

    def test_extra_provider(self):
        self.login()
        extra = {"EXTRA_LLM_BASE_URL": "https://integrate.api.nvidia.com/v1",
                 "EXTRA_LLM_MODEL": "meta/llama-3.3-70b-instruct", "EXTRA_LLM_API_KEY": "nvapi-secret5555"}
        status, data = self.request("POST", "/api/save", {"values": {"GROQ_API_KEY": "gsk_x", **extra}})
        self.assertTrue(json.loads(data)["ok"], data)
        self.assertIn("EXTRA_LLM_API_KEY=nvapi-secret5555", self.env.read_text())
        status, data = self.request("GET", "/api/state")
        self.assertNotIn(b"nvapi-secret5555", data)
        self.assertEqual(json.loads(data)["keys"]["EXTRA_LLM_API_KEY"]["hint"], "…5555")
        # Half-filled is refused; remove_extra clears all three.
        status, data = self.request("POST", "/api/save", {"values": {"EXTRA_LLM_MODEL": ""}})
        self.assertIn("EXTRA_LLM_MODEL must be set to a model name", json.loads(data)["problems"])
        self.request("POST", "/api/save", {"values": {}, "remove_extra": True})
        text = self.env.read_text()
        for key in extra:
            self.assertIn(f"{key}=\n", text)

    def test_rejects_bad_values(self):
        self.login()
        status, data = self.request("POST", "/api/save", {"values": {
            "GROQ_API_KEY": "gsk_x", "TTS_PITCH": "9"}})
        result = json.loads(data)
        self.assertFalse(result["ok"])
        self.assertIn("TTS_PITCH must be a number from 0.8 to 1.5", result["problems"])
        self.assertNotIn("gsk_x", self.env.read_text())

    def test_identity_override(self):
        self.login()
        values = {"GROQ_API_KEY": "gsk_x"}
        self.request("POST", "/api/save", {"values": values, "identity": "You are Robo."})
        identity = Path(self.tmp.name) / "identity.md"
        self.assertEqual(identity.read_text(), "You are Robo.\n")
        default = (REPO / "persona" / "identity.md").read_text()
        self.request("POST", "/api/save", {"values": values, "identity": default})
        self.assertFalse(identity.exists())

    def test_systemd_apply_request(self):
        app = ui.App(self.env, "systemd")
        result = app.save({"values": {"GROQ_API_KEY": "gsk_x"}})
        self.assertTrue(result["ok"])
        self.assertEqual((Path(self.tmp.name) / "apply.request").read_text().strip(), result["apply_request"])

    def test_update_env_appends_new_keys(self):
        ui.update_env(self.env, {"BRAND_NEW": "1"})
        self.assertTrue(self.env.read_text().endswith("BRAND_NEW=1\n"))
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
