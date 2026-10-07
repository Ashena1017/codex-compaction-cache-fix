import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import autostart


class AutostartTests(unittest.TestCase):
    def test_menu_routes_every_action_without_cmd_parsing_chinese(self):
        for selection, action in (("1", "enable"), ("2", "disable"), ("3", "status")):
            with self.subTest(selection=selection), \
                 patch.object(autostart, "manage", return_value={"enabled": False, "task": "test-task"}) as manage, \
                 patch("builtins.input", return_value=selection), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(autostart.menu(), 0)
                self.assertEqual([call.args[0] for call in manage.call_args_list], ["status", action])
                for label in ("1. 开启登录后自启", "2. 关闭登录后自启", "3. 只查看状态", "4. 退出"):
                    self.assertIn(label, output.getvalue())

    def test_menu_exit_and_invalid_selection_never_change_startup(self):
        with patch.object(autostart, "manage", return_value={"enabled": False, "task": "test-task"}) as manage, \
             patch("builtins.input", side_effect=["invalid", "4"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(autostart.menu(), 0)
            manage.assert_called_once_with("status")
            self.assertIn("请输入 1、2、3 或 4。", output.getvalue())

    def test_menu_failure_is_reported_by_entrypoint(self):
        with patch.object(autostart, "manage", side_effect=RuntimeError("synthetic failure")), \
             patch("sys.argv", ["autostart.py", "menu"]), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(autostart.main(), 1)
            self.assertIn("没有完成：synthetic failure", output.getvalue())

    def test_task_name_is_stable_and_separate_per_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(autostart.task_name(folder), autostart.task_name(Path(folder)/'.'))
            self.assertNotEqual(autostart.task_name(folder), autostart.task_name(Path(folder)/'other'))

    def test_enable_requires_settings_and_control(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(autostart.os, 'name', 'nt'):
            with self.assertRaises(RuntimeError):
                autostart.manage('enable', folder)

    @unittest.skipUnless(os.name == 'nt', 'Windows task API wrapper')
    def test_paths_with_spaces_are_passed_as_data_and_task_is_not_run(self):
        with tempfile.TemporaryDirectory(prefix='compact test ') as folder:
            root = Path(folder).resolve()
            (root/'control.py').touch()
            (root/'settings.json').write_text('{}')
            response = MagicMock(returncode=0, stdout=json.dumps({'ok':True, 'enabled':True}))
            with patch.object(autostart.subprocess, 'run', return_value=response) as run:
                self.assertTrue(autostart.manage('enable', root)['enabled'])
            env = run.call_args.kwargs['env']
            self.assertEqual(env['COMPACT_FIX_ARGUMENTS'], subprocess.list2cmdline([str(root/'control.py'), 'start']))
            self.assertTrue(env['COMPACT_FIX_PYTHON'].endswith('pythonw.exe'))
            self.assertNotIn(str(root), autostart.SCRIPT)
            self.assertNotIn('.Run(', autostart.SCRIPT)
            self.assertIn('Task identity mismatch', autostart.SCRIPT)

    @unittest.skipUnless(os.name == 'nt', 'Windows task API wrapper')
    def test_disable_does_not_stop_proxy_process(self):
        response = MagicMock(returncode=0, stdout=json.dumps({'ok':True, 'enabled':False}))
        with patch.object(autostart.subprocess, 'run', return_value=response) as run:
            self.assertFalse(autostart.manage('disable')['enabled'])
        self.assertEqual(run.call_args.kwargs['env']['COMPACT_FIX_ACTION'], 'disable')
        self.assertNotIn('Stop-Process', autostart.SCRIPT)
        self.assertNotIn('.Stop(', autostart.SCRIPT)

    @unittest.skipUnless(os.name == 'nt', 'Windows task API wrapper')
    def test_task_failure_is_reported(self):
        response = MagicMock(returncode=1, stdout=json.dumps({'ok':False,'code':-1}))
        with patch.object(autostart.subprocess, 'run', return_value=response):
            with self.assertRaises(RuntimeError):
                autostart.manage('status')


if __name__ == '__main__':
    unittest.main()
