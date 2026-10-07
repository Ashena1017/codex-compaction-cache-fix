import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from route_sync import ProviderRouteManager


class ProviderRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root/"config.toml"
        self.settings = self.root/"settings.json"
        self.local = "http://127.0.0.1:28615/v1"
        self.write_config('''model_provider = "custom"\n\n[model_providers.custom]\nname = "Current"\nbase_url = "https://tm-token.example/v1"\nexperimental_bearer_token = "synthetic-current-secret"\n\n[model_providers.other]\nname = "Other"\nbase_url = "https://other.example/v1"\nexperimental_bearer_token = "synthetic-other-secret"\n''')
        self.write_settings({"config_path": str(self.config), "local_base_url": self.local,
                             "provider_id": "custom", "upstream_base_url": "https://tm-token.example/v1"})
        self.manager = ProviderRouteManager(self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, value):
        self.config.write_text(value, encoding="utf-8")

    def write_settings(self, value):
        self.settings.write_text(json.dumps(value), encoding="utf-8")

    def read_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_routes_current_provider_and_restores_its_upstream(self):
        self.assertEqual(self.manager.sync(), "https://tm-token.example/v1")
        self.assertEqual(self.manager.expected_authorization(), hashlib.sha256(
            b"Bearer synthetic-current-secret").hexdigest())
        self.assertEqual(self.read_settings()["authorization_sha256"], self.manager.expected_authorization())
        routed = self.config.read_text(encoding="utf-8")
        self.assertIn(f'base_url = "{self.local}"', routed)
        self.assertIn('base_url = "https://other.example/v1"', routed)
        self.assertTrue(self.manager.restore())
        self.assertIn('base_url = "https://tm-token.example/v1"', self.config.read_text(encoding="utf-8"))

    def test_follows_ccswitch_provider_and_restores_new_upstream(self):
        self.manager.sync()
        self.write_config('''model_provider = "other"\n\n[model_providers.custom]\nname = "Old"\nbase_url = "https://tm-token.example/v1"\nexperimental_bearer_token = "synthetic-current-secret"\n\n[model_providers.other]\nname = "Selected by CCSwitch"\nbase_url = "https://wawapii.example/v1"\nexperimental_bearer_token = "synthetic-other-secret"\n''')
        self.assertEqual(self.manager.sync_if_changed(), "https://wawapii.example/v1")
        self.assertEqual(self.manager.expected_authorization(), hashlib.sha256(
            b"Bearer synthetic-other-secret").hexdigest())
        self.assertNotIn("synthetic-other-secret", self.settings.read_text(encoding="utf-8"))
        saved = self.read_settings()
        self.assertEqual(saved["provider_id"], "other")
        self.assertEqual(saved["upstream_base_url"], "https://wawapii.example/v1")
        routed = self.config.read_text(encoding="utf-8")
        self.assertIn('base_url = "https://tm-token.example/v1"', routed)
        self.assertEqual(routed.count(f'base_url = "{self.local}"'), 1)
        self.assertTrue(self.manager.restore())
        self.assertIn('base_url = "https://wawapii.example/v1"', self.config.read_text(encoding="utf-8"))

    def test_rejects_local_loop_and_unrecognized_provider_without_rewriting(self):
        self.write_config(f'''model_provider = "custom"\n\n[model_providers.custom]\nbase_url = "{self.local}"\n''')
        before = self.config.read_text(encoding="utf-8")
        self.write_settings({"config_path": str(self.config), "local_base_url": self.local,
                             "provider_id": "custom", "upstream_base_url": self.local})
        with self.assertRaises(RuntimeError):
            self.manager.discover()
        self.assertEqual(self.config.read_text(encoding="utf-8"), before)
        self.write_config('''model_provider = "custom"\n[model_providers.custom]\nname = "No URL"\n''')
        before = self.config.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.manager.sync()
        self.assertEqual(self.config.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
