import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from config_watch import WindowsConfigWatcher


@unittest.skipUnless(os.name == "nt", "Windows directory notifications required")
class ConfigWatchTests(unittest.TestCase):
    def test_notifies_after_target_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root/"config.toml"
            target.write_text("before", encoding="utf-8")
            notified = threading.Event()
            watcher = WindowsConfigWatcher(target, notified.set)
            watcher.start()
            try:
                deadline = time.monotonic()+2
                while watcher.handle is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(watcher.handle, "watcher did not initialize")
                temporary = root/"config.new"
                temporary.write_text("after", encoding="utf-8")
                temporary.replace(target)
                self.assertTrue(notified.wait(3), "no notification for atomic config replacement")
            finally:
                watcher.stop()


if __name__ == "__main__":
    unittest.main()
