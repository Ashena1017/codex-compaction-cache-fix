"""Scrollable tools that stay accessible at smaller window heights and higher DPI."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ScrollableTools(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Panel.TFrame")
        self.canvas = tk.Canvas(self, width=190, height=180, highlightthickness=0,
                                borderwidth=0, yscrollincrement=24, takefocus=False)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                       style="Panel.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Configure>", self._resize_viewport)
        # Bind to this top-level only; mouse wheel elsewhere must not move these tools.
        self.winfo_toplevel().bind("<MouseWheel>", self._wheel, add="+")

    def _resize_content(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox(self.window))

    def _resize_viewport(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        self._resize_content()

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self:
                if self.content.winfo_reqheight() > self.canvas.winfo_height():
                    step = -max(1, abs(event.delta)//120) if event.delta > 0 else max(1, abs(event.delta)//120)
                    self.canvas.yview_scroll(step, "units")
                return "break"
            widget = getattr(widget, "master", None)

    def keep_visible(self, widget):
        # Keyboard navigation can also reach buttons initially below the viewport.
        self.update_idletasks()
        top = widget.winfo_rooty()-self.content.winfo_rooty()
        bottom = top+widget.winfo_height()
        total = max(self.content.winfo_height(), 1)
        start = self.canvas.canvasy(0)
        end = start+self.canvas.winfo_height()
        if top < start:
            self.canvas.yview_moveto(top/total)
        elif bottom > end:
            self.canvas.yview_moveto((bottom-self.canvas.winfo_height())/total)

    def apply_theme(self, background):
        self.canvas.configure(background=background)


class ScrollablePage(ScrollableTools):
    """A full-width page; scrolling is a fallback for small windows or large fonts."""

    def __init__(self, parent):
        super().__init__(parent)
        self.configure(style="TFrame")
        self.content.configure(style="TFrame", padding=(0, 0, 14, 8))
        self.canvas.configure(width=1, height=1, yscrollincrement=1)

    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self:
                if self.content.winfo_reqheight() > self.canvas.winfo_height():
                    step = -1 if event.delta > 0 else 1
                    self.canvas.yview_scroll(step*max(1, abs(event.delta)//120)*36, "units")
                return "break"
            widget = getattr(widget, "master", None)

    def _resize_content(self, _event=None):
        super()._resize_content(_event)
        if self.content.winfo_reqheight() > self.canvas.winfo_height():
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)
