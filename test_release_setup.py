import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import release_setup


class ReleaseSetupTests(unittest.TestCase):
    def test_release_paths_no_keys_or_config_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root/"config.toml"
            original = b'model_provider="custom"\n[model_providers.custom]\nbase_url="https://example.invalid/v1"\nexperimental_bearer_token="private-placeholder"\n'
            config.write_bytes(original)
            with patch.object(release_setup, "DATA_DIR", root/"data"):
                path = release_setup.initialize_settings(config, root/"runtime"/"python.exe")
                values = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(values["snapshot_path"], str(root/"data"/"prefix-snapshots.dpapi"))
                self.assertEqual(values["python_executable"], str((root/"runtime"/"python.exe").resolve()))
                self.assertNotIn("private-placeholder", path.read_text())
                self.assertEqual(config.read_bytes(), original)
                with self.assertRaises(ValueError):
                    release_setup.initialize_settings(config, root/"different-python.exe")
                self.assertEqual(values, json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
