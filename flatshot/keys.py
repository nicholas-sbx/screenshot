"""Keys inside Flatshot's own windows: the capture overlay and the annotation
editor. Each can be changed in Settings → Shortcuts (kept in the config as
``keys``: action -> key, "" for none). KDE's global shortcuts, which start a
capture, are separate (shortcuts.py). Esc, Enter, and Ctrl+S / Ctrl+C to
capture, stay as they are."""

from flatshot.qt import QKeySequence, Qt, keyval

CAPTURE, EDITOR, BOTH = "capture", "editor", "both"

# action -> (label, default key, where it works); in the order Settings shows them.
BINDINGS = {
    "tool.region": ("Capture region", "R", CAPTURE),
    "tool.record": ("Record screen", "V", CAPTURE),
    "tool.select": ("Select, move and resize drawings", "S", BOTH),
    "tool.pen": ("Pen", "P", BOTH),
    "tool.line": ("Line", "L", BOTH),
    "tool.arrow": ("Arrow", "A", BOTH),
    "tool.rect": ("Rectangle", "B", BOTH),
    "tool.solid": ("Filled rectangle", "F", BOTH),
    "tool.ellipse": ("Ellipse", "E", BOTH),
    "tool.marker": ("Highlighter", "H", BOTH),
    "tool.text": ("Text", "T", BOTH),
    "tool.pixelate": ("Pixelate", "X", BOTH),
    "tool.blur": ("Blur", "U", BOTH),
    "tool.counter": ("Counter", "N", BOTH),
    **{f"color.{i}": (f"Colour {i}: {name}", str(i), BOTH)
       for i, name in enumerate(("ember", "amber", "mint", "sky", "violet", "bone", "ink"), 1)},
    "color.8": ("Your own colour", "8", BOTH),
    "size.down": ("Thinner", "[", BOTH),
    "size.up": ("Thicker", "]", BOTH),
    "undo": ("Undo", "Ctrl+Z", BOTH),
    "redo": ("Redo", "Ctrl+Shift+Z", BOTH),
    "codes": ("Show or hide QR codes", "Q", CAPTURE),
    "pin": ("Pin instead of saving", "K", CAPTURE),
    "snap": ("Snap to edges", "G", CAPTURE),
    "pointer": ("Show or hide the mouse pointer", "M", CAPTURE),
    "copy_color": ("Copy the colour under the pointer", "I", CAPTURE),
    "save": ("Save", "Ctrl+S", EDITOR),
    "save_as": ("Save as", "Ctrl+Shift+S", EDITOR),
    "copy": ("Copy the picture", "Ctrl+C", EDITOR),
    "fit": ("Fit the picture in the window", "Ctrl+0", EDITOR),
    "actual_size": ("Actual size", "Ctrl+1", EDITOR),
    "zoom_in": ("Zoom in", "Ctrl+=", EDITOR),
    "zoom_out": ("Zoom out", "Ctrl+-", EDITOR),
    "close": ("Close the editor", "Ctrl+W", EDITOR),
}
GROUPS = [
    ("Tools", [a for a in BINDINGS if a.startswith("tool.")]),
    ("Colours, sizes and undo", [a for a in BINDINGS if a.startswith(("color.", "size.")) or a in ("undo", "redo")]),
    ("While capturing", ["codes", "pin", "snap", "pointer", "copy_color"]),
    ("Annotation editor", ["save", "save_as", "copy", "fit", "actual_size", "zoom_in", "zoom_out", "close"]),
]

_MODS = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
         | Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.ShiftModifier)
_SHIFT = keyval(Qt.KeyboardModifier.ShiftModifier)


def _parse(text: str) -> tuple[int, int] | None:
    """A key as (key, modifiers), or None for none."""
    seq = QKeySequence(text or "")
    if seq.isEmpty():
        return None
    combo = seq[0]
    return keyval(combo.key()), keyval(combo.keyboardModifiers()) & keyval(_MODS)


def label(text: str) -> str:
    """How a key is shown (as the desktop writes it), "" for none."""
    return QKeySequence(text or "").toString(QKeySequence.SequenceFormat.NativeText)


def overlaps(a: str, b: str) -> bool:
    """Do two actions work in the same window (so can't share a key)?"""
    return BOTH in (BINDINGS[a][2], BINDINGS[b][2]) or BINDINGS[a][2] == BINDINGS[b][2]


class Keymap:
    """The keys in effect: the defaults with the user's changes."""

    def __init__(self, overrides: dict | None = None):
        overrides = overrides if isinstance(overrides, dict) else {}
        self.keys = {}
        for a, (_, default, _) in BINDINGS.items():
            value = overrides.get(a, default)
            self.keys[a] = value if isinstance(value, str) else default
        self._parsed = {a: _parse(k) for a, k in self.keys.items()}

    def label(self, action: str) -> str:
        return label(self.keys.get(action, ""))

    def hint(self, text: str, action: str) -> str:
        """``text`` with its key after it, as the toolbar's tips show it."""
        key = self.label(action)
        return f"{text}  ·  {key}" if key else text

    def action(self, event, where: str) -> str | None:
        """The action ``event`` (a key press) is bound to in ``where``."""
        k = keyval(event.key())
        mods = keyval(event.modifiers()) & keyval(_MODS)
        letter = keyval(Qt.Key.Key_A) <= k <= keyval(Qt.Key.Key_Z)
        for action, parsed in self._parsed.items():
            if parsed is None or BINDINGS[action][2] not in (where, BOTH):
                continue
            key, want = parsed
            if key != k or (mods & ~_SHIFT) != (want & ~_SHIFT):
                continue
            # Shift counts when the key asks for it, and for letters; other
            # keys may need it just to be typed (on some layouts, "]").
            if want & _SHIFT and not mods & _SHIFT:
                continue
            if letter and mods & _SHIFT and not want & _SHIFT:
                continue
            return action
        return None
