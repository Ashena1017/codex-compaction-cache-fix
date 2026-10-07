from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure


class ConfigureTests(unittest.TestCase):
    def config(self, root, url='https://example.invalid/v1'):
        path = root/'config.toml'
        path.write_text('model_provider = "custom"\n[model_providers.custom]\nbase_url = "'+url+'"\nexperimental_bearer_token = "private-test-placeholder"\n')
        return path

    def test_settings_exclude_credentials_and_do_not_change_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.config(root)
            original = path.read_bytes()
            with patch.object(configure, 'ROOT', root):
                value = configure.build_settings(path, port=28616)
            self.assertEqual(value['local_base_url'], 'http://127.0.0.1:28616/v1')
            self.assertEqual(value['snapshot_path'], str(root/'prefix-snapshots.dpapi'))
            self.assertNotIn('private-test-placeholder', str(value))
            self.assertEqual(path.read_bytes(), original)

    def test_existing_proxy_requires_original_upstream(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.config(Path(folder), 'http://127.0.0.1:28615/v1')
            with self.assertRaises(ValueError):
                configure.build_settings(path)
            value = configure.build_settings(path, upstream='https://example.invalid/v1')
            self.assertEqual(value['upstream_base_url'], 'https://example.invalid/v1')

    def test_wrong_upstream_and_credential_urls_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.config(Path(folder))
            for url in ('https://other.invalid/v1', 'https://user:password@example.invalid/v1', 'https://example.invalid/v1?key=test'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    configure.build_settings(path, upstream=url)

    def test_invalid_port_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.config(Path(folder))
            for port in (0, 65536):
                with self.assertRaises(ValueError):
                    configure.build_settings(path, port=port)

    def test_builtin_provider_without_custom_url_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.toml'
            path.write_text('model_provider = "openai"\n')
            with self.assertRaises(ValueError):
                configure.build_settings(path)


if __name__ == '__main__':
    unittest.main()
