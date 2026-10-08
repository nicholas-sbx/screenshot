"""KDE global shortcuts through KGlobalAccel (org.kde.kglobalaccel).

Flatshot registers as the component "flatshot", so its shortcuts also show
up — and can be changed — in System Settings → Shortcuts. Presses arrive
as the component's ``globalShortcutPressed`` signal while the tray app runs.
"""

from flatshot import dbus
from flatshot.qt import QKeyCombination, QKeySequence, QObject, Signal

SERVICE = "org.kde.kglobalaccel"
PATH = "/kglobalaccel"
IFACE = "org.kde.KGlobalAccel"
COMPONENT_IFACE = "org.kde.kglobalaccel.Component"
COMPONENT = "flatshot"
FRIENDLY = "Flatshot"

# action -> (label, default key sequence or "")
ACTIONS = {
    "region": ("Capture region", "Ctrl+Print"),
    "screen": ("Capture all screens", "Ctrl+Shift+Print"),
}

# KGlobalAccel::SetShortcutFlag
IS_DEFAULT, SET_PRESENT, NO_AUTOLOADING = 1, 2, 4


def _action_id(action: str) -> list[str]:
    return [COMPONENT, action, FRIENDLY, ACTIONS[action][0]]


def to_keys(text: str) -> list[int]:
    """'Ctrl+Print' -> [combined key int, ...] (one per chord)."""
    seq = QKeySequence(text)
    return [seq[i].toCombined() for i in range(seq.count())]


def to_text(keys: list[int]) -> str:
    if not keys:
        return ""
    combos = [QKeyCombination.fromCombined(k) for k in keys[:4]]
    return QKeySequence(*combos).toString(QKeySequence.SequenceFormat.PortableText)


def _wire(seqs: list[str]) -> list[tuple]:
    """Key sequences as KGlobalAccel's a(ai): always exactly four ints per
    sequence — the daemon reads four unconditionally and aborts on fewer."""
    return [((to_keys(s) + [0, 0, 0, 0])[:4],) for s in seqs if s]


def _unwire(value) -> list[str]:
    return [to_text([k for k in keys if k]) for (keys,) in value if any(keys)]


class GlobalShortcuts(QObject):
    triggered = Signal(str)  # action name
    changed = Signal()  # assignments changed (here or in System Settings)

    def __init__(self):
        super().__init__()
        self.active = False
        self.error = ""
        self._listener = None

    def supported(self) -> bool:
        return dbus.available() and (dbus.has_owner(SERVICE) or self._activatable())

    def _call(self, method, sig, *args):
        return dbus.call(SERVICE, PATH, IFACE, method, sig, *args)

    def _set(self, action: str, seqs: list[str], flags: int) -> list[str]:
        aid = _action_id(action)
        try:
            return _unwire(self._call("setShortcutKeys", "asa(ai)u", aid, _wire(seqs), flags)[0])
        except dbus.DBusError as e:
            if "UnknownMethod" not in str(e):
                raise
            # Older kglobalaccel: one int per shortcut.
            keys = [to_keys(s)[0] for s in seqs if s]
            return [to_text([k]) for k in self._call("setShortcut", "asaiu", aid, keys, flags)[0] if k]

    def start(self) -> bool:
        """Register our actions and start listening. False if unavailable."""
        if not dbus.available():
            self.error = "python jeepney module is missing"
            return False
        if not dbus.has_owner(SERVICE) and not self._activatable():
            self.error = "KDE global shortcut service is not running"
            return False
        try:
            for action, (_, default) in ACTIONS.items():
                self._call("doRegister", "as", _action_id(action))
                defaults = [default] if default else []
                self._set(action, defaults, IS_DEFAULT)
                # Without NoAutoloading the daemon keeps the user's saved keys.
                self._set(action, defaults, SET_PRESENT)
            path = self._call("getComponent", "s", COMPONENT)[0]
        except dbus.DBusError as e:
            self.error = str(e)
            return False
        self._listener = dbus.SignalListener([
            {"interface": COMPONENT_IFACE, "member": "globalShortcutPressed", "path": path},
            {"interface": IFACE, "member": "yourShortcutsChanged", "path": PATH},
        ])
        self._listener.received.connect(self._on_signal)
        self.active = self._listener.start()
        if not self.active:
            self.error = "could not listen on the session bus"
        return self.active

    def _activatable(self) -> bool:
        try:
            names = dbus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "ListActivatableNames")[0]
        except dbus.DBusError:
            return False
        return SERVICE in names

    def _on_signal(self, iface, member, body):
        if member == "globalShortcutPressed" and len(body) >= 2 and body[0] == COMPONENT:
            if body[1] in ACTIONS:
                self.triggered.emit(body[1])
        elif member == "yourShortcutsChanged" and body and list(body[0])[:1] == [COMPONENT]:
            self.changed.emit()

    def get(self, action: str) -> str:
        try:
            keys = _unwire(self._call("shortcutKeys", "as", _action_id(action))[0])
        except dbus.DBusError:
            return ""
        return keys[0] if keys else ""

    def assign(self, action: str, sequence: str) -> tuple[bool, str]:
        """Set (or clear, with "") an action's shortcut. Returns (ok, now-active)."""
        try:
            result = self._set(action, [sequence] if sequence else [], SET_PRESENT | NO_AUTOLOADING)
        except dbus.DBusError as e:
            return False, self.get(action) if self.active else str(e)
        now = result[0] if result else ""
        self.changed.emit()
        return (now == sequence), now

    def owner_of(self, sequence: str, action: str = "") -> str:
        """Friendly name of whatever else already uses ``sequence``, if anything."""
        keys = to_keys(sequence)
        if not keys:
            return ""
        try:
            hits = self._call("getGlobalShortcutsByKey", "i", keys[0])[0]
        except dbus.DBusError:
            return ""
        # KGlobalShortcutInfo on the wire: (uniqueName, friendlyName, componentUnique,
        #   componentFriendly, contextUnique, contextFriendly, keys, defaultKeys)
        for hit in hits:
            if (hit[2], hit[0]) != (COMPONENT, action):
                return f"{hit[3]} › {hit[1]}"
        return ""

    def block(self, blocked: bool) -> None:
        """Suspend every global shortcut (while recording a new one)."""
        try:
            self._call("blockGlobalShortcuts", "b", blocked)
        except dbus.DBusError:
            pass
