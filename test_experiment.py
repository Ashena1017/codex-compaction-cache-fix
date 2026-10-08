import hashlib
import unittest
from unittest.mock import MagicMock, patch

import experiment


class ExperimentServerTests(unittest.TestCase):
    def test_server_is_initialized_with_active_provider_credential_hash(self):
        server = MagicMock()
        key = "synthetic-test-key"

        with patch.object(experiment, "make_server", return_value=server) as create_server:
            result = experiment.make_experiment_server("https://provider.example/v1", key)

        create_server.assert_called_once_with("https://provider.example/v1", 0, snapshot_path=None)
        server.state.set_upstream.assert_called_once_with(
            "https://provider.example/v1",
            hashlib.sha256(("Bearer "+key).encode()).hexdigest())
        self.assertIs(result, server)


if __name__ == "__main__":
    unittest.main(verbosity=2)
