"""UI regressions; all service/config-changing operations are mocked."""
import tkinter as tk
import unittest
from unittest.mock import patch

import gui


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.refresh_patch = patch.object(gui.ControlPanel, 'refresh', lambda self: None)
        self.tick_patch = patch.object(gui.ControlPanel, '_tick', lambda self: None)
        self.refresh_patch.start()
        self.tick_patch.start()
        self.addCleanup(self.refresh_patch.stop)
        self.addCleanup(self.tick_patch.stop)
        try:
            self.panel = gui.ControlPanel()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(self.close)
        self.panel.update()

    def close(self):
        for job in self.panel.tk.call('after', 'info'):
            self.panel.after_cancel(job)
        self.panel.destroy()

    def test_returning_from_logs_remaps_home_at_small_size(self):
        panel = self.panel
        panel.geometry('900x620')
        for key in ('logs', 'home', 'sync', 'startup', 'diagnostics', 'home'):
            panel.nav_buttons[key].invoke()
            panel.update()
            self.assertTrue(panel.pages[key].winfo_ismapped(), key)
            self.assertTrue(all(not page.winfo_ismapped() for name, page in panel.pages.items() if name != key))
        page = panel.pages['home']
        last = page.content.winfo_children()[-1].winfo_children()[-1]
        page.keep_visible(last)
        panel.update()
        self.assertGreaterEqual(last.winfo_rooty(), page.canvas.winfo_rooty())
        self.assertLessEqual(last.winfo_rooty()+last.winfo_height(), page.canvas.winfo_rooty()+page.canvas.winfo_height())

    def test_running_service_does_not_imply_codex_is_using_it(self):
        self.panel._display_state({'running': True, 'local': False})
        self.assertEqual(self.panel.proxy_var.get(), '运行中')
        self.assertEqual(self.panel.route_var.get(), '供应商直连')
        self.assertIn('仍是直连', self.panel.status_var.get())
        self.panel._show_status(1, '没有完成：配置文件无法读取')
        self.assertEqual(self.panel.route_var.get(), '检查失败')
        self.panel._display_state({'running': False, 'local': True})
        self.assertIn('代理未运行', self.panel.status_var.get())

    def test_actions_keep_cli_arguments_and_paid_experiment_confirmation(self):
        calls = []
        with patch.object(self.panel, '_run', side_effect=lambda args, *a, **kw: calls.append(args)), \
             patch.object(gui.messagebox, 'askyesno', return_value=False):
            self.panel.sync_provider()
            self.panel.startup('status')
            self.panel.set_mode('manual')
            self.panel._paid_experiment('experiment.py', 'test')
        self.assertEqual(calls, [[gui.APP_DIR/'control.py', 'sync-provider'],
                                 [gui.APP_DIR/'autostart.py', 'status'],
                                 [gui.APP_DIR/'control.py', 'set-sync-mode', '--mode', 'manual']])
        self.panel._set_busy(True)
        self.assertTrue(all(widget.instate(['disabled']) for widget in self.panel.action_widgets))
        self.panel._set_busy(False)
        self.assertTrue(all(not widget.instate(['disabled']) for widget in self.panel.action_widgets))


if __name__ == '__main__':
    unittest.main()
