"""A bounded live Text view that preserves the reader's position while paused."""
import tkinter as tk
from tkinter import ttk

from utils.helpers import SmoothScroller, create_gradient_button
from utils.i18n import trp


def create_log_button(parent, text, command, *, primary=False, backdrop=None):
    """Use the application's rounded buttons for both log windows."""
    button = create_gradient_button(
        parent, text, command,
        colors=("#2688d7", "#2466b4") if primary else ("#607d8b", "#78909c"),
        height=32 if primary else 28, font=("微软雅黑", 9, "bold"))
    button.configure(takefocus=True, cursor="hand2")
    button.invoke = command

    def activate(_event):
        command()
        return "break"

    button.bind("<Return>", activate)
    button.bind("<space>", activate)
    button.bind("<FocusIn>", lambda _e: button.event_generate("<Enter>"))
    button.bind("<FocusOut>", lambda _e: button.event_generate("<Leave>"))
    if backdrop is not None:
        original_setter = button.set_corner_bg
        # The floating button sits over the Text, whose background can differ
        # from its containing frame. Keep all four corners blended on theme changes.
        button.set_corner_bg = lambda _bg: original_setter(backdrop.cget("bg"))
        button.set_corner_bg(None)
    return button


class LiveLogView:
    def __init__(self, text, container, store, theme):
        self.text, self.container, self.store = text, container, store
        self.following = True
        self.unread = 0
        self.sequence = 0
        self.generation = store.generation
        self._mutating = False
        self._idle = None
        self._closed = False
        self.button = create_log_button(
            container, trp("↓ 回到底部"), self.jump_to_bottom,
            primary=True, backdrop=text)
        self.scroller = SmoothScroller.for_text(text, on_user_scroll=self._wheel,
                                                on_settle=self._settled)
        text.bind("<MouseWheel>", self._wheel_event)
        # Windows' native Scrollbar ignores dark trough colors. Give only the
        # live log views a themed, arrow-free scrollbar with a matching track.
        old_bar = text.vbar
        text.vbar = ttk.Scrollbar(text.frame, orient="vertical", command=self._scrollbar,
                                  style="Log.Vertical.TScrollbar")
        text.vbar.pack(side="right", fill="y", before=text._w)
        old_bar.destroy()
        text.configure(yscrollcommand=self._scrolled, undo=False)
        self.set_theme(theme)
        for key in ("Up", "Down", "Prior", "Next", "Home", "End"):
            text.bind("<KeyPress-" + key + ">", self._keyboard, add="+")
        text.bind("<Button-1>", self._selecting, add="+")
        text.bind("<Destroy>", self._destroy, add="+")

    def set_theme(self, theme):
        if self._closed:
            return
        bg = theme["log_bg"]
        style = ttk.Style(self.text)
        # Derive the elements from clam even before the main window applies its
        # theme, so native Windows drawing cannot reintroduce the light track.
        for part in ("trough", "thumb"):
            element = "Log.Vertical.Scrollbar." + part
            if element not in style.element_names():
                style.element_create(element, "from", "clam", "Vertical.Scrollbar." + part)
        style.layout("Log.Vertical.TScrollbar", [
            ("Log.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Log.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        thumb = theme.get("button_bg", "#808080")
        style.configure("Log.Vertical.TScrollbar", background=thumb, troughcolor=bg,
                        bordercolor=bg, lightcolor=thumb, darkcolor=thumb,
                        borderwidth=0, relief="flat", width=10, arrowsize=10,
                        gripcount=0)
        style.map("Log.Vertical.TScrollbar", background=[
            ("pressed", theme.get("card_sel_bar", "#2f7fd1")),
            ("active", theme.get("muted_fg", "#808080"))])
        self.text.configure(bg=bg, fg=theme["log_fg"])
        self.text.frame.configure(bg=bg)
        self.button.set_corner_bg(None)

    def at_bottom(self):
        try:
            if self.text.compare("end-1c", "==", "1.0"):
                return True
            # A fraction tolerance scales badly with long logs: require the end.
            return self.text.yview()[1] >= 1.0 - 1e-7
        except tk.TclError:
            return True

    def pause(self):
        self.following = False
        self._show_button()

    def _wheel(self, going_up):
        if going_up:
            self.pause()

    def _wheel_event(self, event):
        # Pause on the input direction even if a previous downward animation
        # still has distance left; waiting for the net target can miss reversal.
        if (getattr(event, "delta", 0) or 0) > 0:
            self.pause()
        return self.scroller.wheel(event)

    def _scrollbar(self, *args):
        self.scroller.stop()
        self.pause()
        self.text.yview(*args)
        self._settled()

    def _keyboard(self, event):
        self.scroller.stop()
        self.pause()
        self.text.after_idle(self._settled)

    def _selecting(self, _event):
        # Selection/autoscroll should not compete with new output.
        if not self.at_bottom():
            self.pause()

    def _scrolled(self, first, last):
        self.text.vbar.set(first, last)
        if self.text.compare("end-1c", "==", "1.0"):
            return
        if not self._mutating and not self.scroller.scrolling():
            if float(last) < 1.0 - 1e-7:
                self.following = False
            elif not self.following:
                self.jump_to_bottom()
        self._show_button()

    def _settled(self):
        if self._closed:
            return
        if self.at_bottom():
            self.jump_to_bottom()
        else:
            self.pause()

    def _show_button(self):
        if self._closed:
            return
        visible = not self.following and (self.unread or not self.at_bottom())
        if visible:
            caption = trp("↓ 新日志 {0} 行", self.unread) if self.unread else trp("↓ 回到底部")
            if self.button.get_text() != caption:
                self.button.set_text(caption)
            # ScrolledText.__str__ names its outer frame; use the actual Text
            # path so the button stays inside the viewport, clear of the scrollbar.
            self.button.place(in_=self.text._w, relx=1.0, rely=1.0,
                              x=-10, y=-10, anchor="se")
            self.button.tk.call('raise', self.button._w)
        else:
            self.button.place_forget()

    def refresh(self, force=False):
        if self._closed:
            return
        generation, sequence = self.store.status()
        reset = generation != self.generation
        if reset:
            self.scroller.stop()
            self.following = True
            self.unread = 0
        if not self.following and not force:
            self.unread = max(0, sequence - self.sequence)
            self._show_button()
            return
        if sequence == self.sequence and not reset and not force:
            return
        generation, sequence, rows = self.store.snapshot(self.sequence)
        rebuild = reset or (rows and rows[0][0] > self.sequence + 1)
        self._mutating = True
        if self._idle is not None:
            self.text.after_cancel(self._idle)
        try:
            self.text.configure(state="normal")
            if rebuild:
                self.text.delete("1.0", "end")
            # One Tcl insertion per batch, preserving semantic colors.
            args = []
            for _, level, line in rows:
                args.extend((line, (level,)))
            if args:
                self.text.insert("end", *args)
            count = int(self.text.index("end-1c").split(".")[0]) - 1
            if count > self.store.limit:
                self.text.delete("1.0", "%d.0" % (count - self.store.limit + 1))
            self.text.configure(state="disabled")
            self.text.yview_moveto(1.0)
            self.generation, self.sequence = generation, sequence
            self.unread = 0
        finally:
            self.text.configure(state="disabled")
            self._idle = self.text.after_idle(self._finish_update)
        self._show_button()

    def _finish_update(self):
        self._idle = None
        if self.following:
            self.text.yview_moveto(1.0)
        self._mutating = False

    def jump_to_bottom(self):
        if self._closed:
            return
        self.scroller.stop()
        self.following = True
        self.refresh(force=True)
        self.text.yview_moveto(1.0)
        self._show_button()

    def _destroy(self, event):
        if event.widget is not self.text:
            return
        self._closed = True
        self.scroller.stop()
        if self._idle is not None:
            self.text.after_cancel(self._idle)
            self._idle = None
