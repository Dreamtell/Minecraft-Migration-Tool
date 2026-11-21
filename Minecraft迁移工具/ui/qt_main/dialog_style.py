"""Theme helpers for Qt child dialogs."""

from PySide6 import QtWidgets

from ui.qt_main import look


def theme_for(dialog, name=None):
    """Return the parent theme, using name only when no themed parent exists."""
    node = dialog
    inherited = None
    while node is not None:
        value = getattr(node, "theme", None)
        if isinstance(value, dict) and value.get("bg"):
            inherited = value
            break
        node = node.parent()

    if inherited is not None:
        return inherited

    dark = name == "dark"
    return {
        "bg": "#2e2e2e" if dark else "#f0f0f0",
        "muted_fg": "#aaaaaa" if dark else "#6b6b6b",
    }


def apply_button_styles(dialog, theme=None):
    """Install the shared semantic button rules and repolish marked buttons."""
    theme = theme if isinstance(theme, dict) else theme_for(dialog)
    dialog.setStyleSheet(look.button_rules(theme))
    for label in dialog.findChildren(QtWidgets.QLabel):
        if label.property("mutedText"):
            set_muted_label(label, theme)
    for button in dialog.findChildren(QtWidgets.QPushButton):
        kind = button.property("kind")
        if kind:
            look.style_button(button, str(kind), bool(button.property("iconOnly")))


def set_button_kind(button, kind):
    look.style_button(button, kind)


def muted_color(theme):
    return str(theme.get("muted_fg", "#6b6b6b"))


def set_muted_label(label, theme, font_size=11):
    label.setProperty("mutedText", True)
    label.setStyleSheet("color: %s; font-size: %dpx;" %
                        (muted_color(theme), int(font_size)))
