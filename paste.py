"""
Paste the clipboard into the foreground app.

Windows: Ctrl+V through the Win32 SendInput API. Sending the keystroke
directly keeps pyautogui (and its ~20 MB of screenshot and mouse
dependencies) out of the tray process. Scan codes are supplied alongside the
virtual keys because a few targets (games, some Electron apps) read the scan
code rather than the VK.

macOS: Cmd+V through pynput, which needs the Accessibility permission.
"""
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    VK_CONTROL = 0x11
    VK_V = 0x56

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    MAPVK_VK_TO_VSC = 0

    ULONG_PTR = wintypes.WPARAM

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _MOUSEINPUT(ctypes.Structure):
        # Present only so the union - and therefore sizeof(INPUT) - matches what
        # SendInput expects; it rejects any other cbSize.
        _fields_ = [("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD),
                    ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT

    def _key(vk: int, up: bool) -> INPUT:
        ev = INPUT(type=INPUT_KEYBOARD)
        ev.ki = _KEYBDINPUT(
            wVk=vk,
            wScan=_user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC),
            dwFlags=KEYEVENTF_KEYUP if up else 0,
            time=0,
            dwExtraInfo=0,
        )
        return ev

    def paste_from_clipboard() -> bool:
        """Send Ctrl+V to the foreground window. Returns True if it was accepted."""
        events = (INPUT * 4)(
            _key(VK_CONTROL, False),
            _key(VK_V, False),
            _key(VK_V, True),
            _key(VK_CONTROL, True),
        )
        sent = _user32.SendInput(4, events, ctypes.sizeof(INPUT))
        if sent == 4:
            return True

        # Something blocked the injection (UIPI against an elevated window, or a
        # low-level hook). Release the modifier so Ctrl can't be left stuck down.
        stuck = (INPUT * 2)(_key(VK_V, True), _key(VK_CONTROL, True))
        _user32.SendInput(2, stuck, ctypes.sizeof(INPUT))
        return False

else:
    def paste_from_clipboard() -> bool:
        """Send Cmd+V (macOS) or Ctrl+V to the frontmost app."""
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.cmd if sys.platform == "darwin" else Key.ctrl):
            kb.press("v")
            kb.release("v")
        return True
