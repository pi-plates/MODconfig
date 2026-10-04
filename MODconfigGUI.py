#!/usr/bin/env python3
"""MODconfigGUI.py - a minimal serial terminal emulator (PuTTY-style) for
MODplateR1's USB "Config" CDC interface (the interactive menu served by the
firmware's cdc_menu.c - the same menu the web Configuration page mirrors).

On startup this scans the system's COM ports for MODplateR1's config port
(VID/PID + interface identity - see find_port()) and connects to it
automatically - no port name needed. This is the GUI counterpart to
MODconfig.py: same port-detection logic and same close-on-exit behavior,
but renders cdc_menu.c's own ANSI colors in a window instead of leaving raw
escape codes in a console.

Like a raw PuTTY serial session, this does no local echo: cdc_menu.c echoes
back everything typed (see read_line_ex() in cdc_menu.c), so what you type
appears on screen only once the board sends it back. Keystrokes are sent to
the board as-is; Enter sends '\r', Backspace/Delete send 0x08/0x7F - all of
which cdc_menu.c's read_line_ex()/read_selection() already understand.

Requires pyserial:  pip install pyserial

Usage:
    python MODconfigGUI.py
"""

import re
import sys
import threading
import queue
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

import serial
import serial.tools.list_ports

VID = 0x2E8A
PID = 0x1158
BAUD = 115200  # USB CDC ignores the actual value, but pyserial requires one

# CDC0's USB interface string (see usb_descriptors.c's STRID_CDC0) and the
# control-interface numbers for both CDC ports (ITF_NUM_CDC_0/1) - see
# find_port() below for why port detection needs all of this instead of a
# plain VID:PID match (the board exposes a second, debug-only CDC port
# sharing the same VID:PID).
CONFIG_INTERFACE_NAME = "MODplate Config"
CONFIG_INTERFACE_NUMBER = 0
DEBUG_INTERFACE_NUMBER = 2

APP_NAME = "MODplate Configuration"

def _pick_font(root, preferred, fallback, size, weight="normal"):
    """Returns the first font family in `preferred` that's actually
    installed, or `fallback` (one of Tk's own built-in generic fonts,
    always available on every platform) if none of them are - needed
    because this app's fonts were originally picked for Windows only
    (Consolas, Segoe UI), neither of which exists on macOS. There, Tk
    silently substitutes some other, typically wider and not necessarily
    monospace, font instead - which both breaks the terminal's fixed-width
    column alignment (the Attached Plates / Register Map listings) and
    wraps menu lines early, since the window was sized assuming Consolas's
    narrower glyphs. Must be called after a Tk root exists - querying
    installed fonts needs one.
    """
    available = set(tkfont.families(root))
    family = next((f for f in preferred if f in available), fallback)
    return (family, size, weight) if weight != "normal" else (family, size)

# Window/taskbar icon - the Pi-Plates logo's circular pi mark (see
# logo_data.h), cropped square and pre-rendered at two sizes with a
# proper resample (Tk's own PhotoImage scaling is nearest-neighbor only
# and looks blocky) rather than scaling one image live.
_ICON_PNG_B64_64 = (
    "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"
)
_ICON_PNG_B64_32 = (
    "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"
    "wmnL/g8pBAuKHmkNdgAAAABJRU5ErkJggg=="
)

# Pi-Plates logo (the same PNG the web config pages show next to their
# heading - see logo_data.h), embedded here as base64 for the same reason
# as the icon above: keeps the PyInstaller --onefile .exe self-contained.
_LOGO_PNG_B64 = (
    "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"
    "AAAAAElFTkSuQmCC"
)

# cdc_menu.c draws its own color scheme with ANSI SGR escape codes (see its
# ANSI_DARK_GREEN_WHITE/ANSI_RESET) - xterm 256-color indices 15 (white) and
# 22 (dark green), sent as e.g. "\x1b[48;5;22m\x1b[38;5;15m". Matched here
# exactly (see _xterm256_to_hex()) rather than picking our own colors, so
# what this terminal shows is exactly what the firmware intends.
BG_COLOR = "#005F00"   # xterm-256 color 22 - the firmware's own default background
FG_COLOR = "#FFFFFF"   # xterm-256 color 15 - the firmware's own default foreground

_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")

# Text the board's cdc_menu.c prints right before each way a session ends -
# on plain Exit, and on either restart path (both share "Restarting..."
# since cdc_menu.c's confirm() gate means the operator has just deliberately
# chosen to reboot the board). Watched for below so this window closes
# itself the moment the board announces it, instead of being left open on a
# connection that's already gone.
_CLOSE_TRIGGERS = (
    ("Exiting configuration menu", None, 400),
    ("Restarting...", "\n*** Board is restarting - closing this window... ***\n", 2000),
)
_MAX_TRIGGER_LEN = max(len(marker) for marker, _, _ in _CLOSE_TRIGGERS)


def _xterm256_to_hex(n):
    """Converts an xterm 256-color palette index to a "#RRGGBB" string -
    covers the whole 0-255 range (standard 16, the 6x6x6 color cube, and the
    greyscale ramp), even though cdc_menu.c currently only ever sends 15 and
    22."""
    if n < 16:
        basic = ("#000000", "#CD0000", "#00CD00", "#CDCD00", "#0000EE", "#CD00CD", "#00CDCD", "#E5E5E5",
                  "#7F7F7F", "#FF0000", "#00FF00", "#FFFF00", "#5C5CFF", "#FF00FF", "#00FFFF", "#FFFFFF")
        return basic[n]
    if n < 232:
        n -= 16
        r, g, b = n // 36, (n % 36) // 6, n % 6
        levels = (0, 95, 135, 175, 215, 255)
        return "#{:02x}{:02x}{:02x}".format(levels[r], levels[g], levels[b])
    level = 8 + (n - 232) * 10
    return "#{:02x}{:02x}{:02x}".format(level, level, level)


_LOCATION_INTERFACE_RE = re.compile(r"\.(\d+)$")


def _location_interface_number(p):
    """The trailing USB topology interface number from p.location (e.g.
    "1-13.2:x.2" -> 2), or None if p.location isn't populated or doesn't
    look like that. Observed in practice: Windows only populates location
    for one of this board's two CDC ports at a time (which one varies) -
    never both - so this is also used for elimination in find_port(), not
    just a direct match."""
    if not p.location:
        return None
    m = _LOCATION_INTERFACE_RE.search(p.location)
    return int(m.group(1)) if m else None


def _port_matches_interface(p, interface_name, interface_number):
    """True if `p` (a pyserial ListPortInfo, already VID/PID-matched) looks
    like the named CDC interface, tried in order of reliability:

    1. p.interface - the real USB interface string descriptor, populated on
       Linux/macOS (authoritative there); always None on Windows in current
       pyserial versions, so this check is simply unavailable there.
    2. p.description - Windows' device "friendly name". Observed in
       practice, on this exact board, to not reliably carry the interface
       string - kept as a fallback only, don't rely on it.
    3. p.location's trailing USB topology interface number (e.g.
       "1-9.3:x.0" -> 0) - populated on both Windows and Linux, derived from
       actual USB topology rather than driver-supplied text. This is what
       actually distinguishes the two CDC interfaces on Windows in practice.
    """
    if p.interface and interface_name in p.interface:
        return True
    if p.description and interface_name in p.description:
        return True
    return _location_interface_number(p) == interface_number


def _is_debug_port(port_name, listen_s=1.5):
    """Last-resort disambiguation, if _port_matches_interface() couldn't
    tell the candidates apart (e.g. p.location isn't populated on this
    OS/driver either): distinguish by behaviour instead. The debug port
    (CDC1) emits an unsolicited status line once a second entirely on its
    own (see debug_printf() in MODplateR1.c's main loop); the config
    console (CDC0) never sends anything unless the operator interacts with
    it. Listening briefly - never writing, which on the console could
    itself trigger opening its menu and leave the board's main loop stalled
    waiting for a session nobody continues - tells them apart with
    certainty.
    """
    try:
        with serial.Serial(port_name, BAUD, timeout=listen_s) as probe:
            return len(probe.read(1)) > 0
    except (serial.SerialException, OSError):
        return None  # couldn't open it to check


def find_port():
    """Finds the config menu console (CDC interface 0) specifically. The
    board exposes two CDC ports sharing one VID:PID - this one (the
    interactive menu) and a second, write-only debug log stream - so a
    plain VID:PID match alone is no longer enough to tell them apart.
    """
    candidates = [p for p in serial.tools.list_ports.comports() if p.vid == VID and p.pid == PID]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].device

    for p in candidates:
        if _port_matches_interface(p, CONFIG_INTERFACE_NAME, CONFIG_INTERFACE_NUMBER):
            return p.device

    # Nothing positively matched the console - but Windows has been observed
    # to populate p.location for only one of the two ports at a time (the
    # other comes back None), so if exactly one candidate is positively
    # identified as the DEBUG port by location, the other one is the console
    # by elimination, even though its own location is blank.
    if len(candidates) == 2:
        known_debug = [p for p in candidates if _location_interface_number(p) == DEBUG_INTERFACE_NUMBER]
        if len(known_debug) == 1:
            return next(p for p in candidates if p is not known_debug[0]).device

    # Still ambiguous - fall back to the behavioral probe.
    inconclusive = []
    for p in candidates:
        is_debug = _is_debug_port(p.device)
        if is_debug is False:
            return p.device  # stayed silent for over a second - this is the console
        if is_debug is None:
            inconclusive.append(p)

    # Every candidate looked like the debug port, or couldn't be opened to
    # check (e.g. briefly busy) - last resort, pick the lowest-sorted device
    # name among whichever candidates the probe couldn't rule out.
    fallback_pool = inconclusive or candidates
    fallback_pool.sort(key=lambda p: p.device)
    return fallback_pool[0].device


class TerminalApp:
    def __init__(self, root, ser):
        self.root = root
        self.ser = ser
        self.stop_event = threading.Event()
        self.rx_queue = queue.Queue()
        self.closing = False
        self._tail = ""  # small rolling window of recently displayed text,
                          # so a close trigger can be found even if it's
                          # split across two separate queue reads
        self._ansi_carry = ""   # trailing bytes that might be an incomplete
                                 # escape sequence, held for the next chunk
        self._cur_fg = None     # None = FG_COLOR (the SGR "reset" state)
        self._cur_bg = None     # None = BG_COLOR
        self._known_tags = set()

        root.title(f"{APP_NAME} - {ser.port}")
        root.geometry("1200x600")
        root.configure(bg=BG_COLOR)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Window/taskbar icon - the logo's own circular pi mark (see
        # _ICON_PNG_B64_64/32 above). Kept as attributes (not just locals)
        # so Tk doesn't garbage collect the images out from under the
        # window after this returns. Passing both sizes lets Tk/Windows
        # pick whichever fits a given icon slot (title bar vs. taskbar vs.
        # Alt-Tab) instead of scaling one image up or down.
        try:
            self._icon_image_64 = tk.PhotoImage(data=_ICON_PNG_B64_64)
            self._icon_image_32 = tk.PhotoImage(data=_ICON_PNG_B64_32)
            root.iconphoto(True, self._icon_image_64, self._icon_image_32)
        except tk.TclError:
            pass  # icon is cosmetic - a theme/Tk build that can't load it shouldn't block the app

        # Logo banner - same Pi-Plates logo and layout (image beside the
        # app name) as the web config pages' own header. subsample(3) is a
        # blocky/nearest-neighbor downscale (Tk's PhotoImage has no smooth
        # resize built in), but it's only ~267x83 here so that's not
        # noticeable. Skipped entirely, not just left blank, if it can't be
        # loaded - cosmetic, shouldn't block the app.
        try:
            self._logo_image = tk.PhotoImage(data=_LOGO_PNG_B64).subsample(3, 3)
            # White background (not the terminal's dark green) - the logo's
            # own background is white, so it'd otherwise show a green box
            # around it.
            banner = tk.Frame(root, bg="white")
            banner.pack(fill="x")
            heading_font = _pick_font(root, ["Segoe UI", "Helvetica Neue", "Helvetica", "Arial"],
                                       "TkDefaultFont", 18, "bold")
            tk.Label(banner, image=self._logo_image, bg="white").pack(side="left")
            tk.Label(banner, text=APP_NAME, bg="white", fg="black",
                     font=heading_font).pack(side="left", padx=(12, 0))
        except tk.TclError:
            pass

        mono_font = _pick_font(root, ["Consolas", "Menlo", "DejaVu Sans Mono", "Courier New", "Monaco"],
                                "TkFixedFont", 16)

        frame = tk.Frame(root, bg=BG_COLOR)
        frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        self.text = tk.Text(
            frame,
            bg=BG_COLOR,
            fg=FG_COLOR,
            insertbackground=FG_COLOR,
            font=mono_font,
            wrap="word",
            yscrollcommand=scrollbar.set,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

        # Raw terminal: every keystroke is sent to the board instead of
        # being inserted locally - "break" stops the Text widget's own
        # default insert-on-keypress behavior. What you type only appears
        # once cdc_menu.c echoes it back (see this file's docstring).
        self.text.bind("<Key>", self.on_key)
        self.text.focus_set()

        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.root.after(20, self._poll_queue)

        # cdc_menu.c's menu only opens once Enter is pressed (see
        # cdc_menu_service() in MODplateR1.c) - sends one automatically
        # shortly after opening the port, so the menu appears without the
        # user needing to press anything first. The short delay gives the
        # OS's CDC ACM driver a moment to settle after opening.
        self.root.after(100, self._send_initial_enter)

    def _send_initial_enter(self):
        try:
            self.ser.write(b"\r")
        except (serial.SerialException, OSError):
            self._show_disconnected()

    def _read_loop(self):
        while not self.stop_event.is_set():
            try:
                n = self.ser.in_waiting
                data = self.ser.read(n if n else 1)
            except (serial.SerialException, OSError):
                self.rx_queue.put(None)  # signals disconnect to the UI thread
                return
            if data:
                self.rx_queue.put(data)

    def _poll_queue(self):
        try:
            while True:
                item = self.rx_queue.get_nowait()
                if item is None:
                    self._show_disconnected()
                    return
                text = item.decode("utf-8", errors="replace")
                self._ansi_carry = self._process_ansi(self._ansi_carry + text)
        except queue.Empty:
            pass
        # Keeps polling right up until on_close() actually runs (scheduled
        # above, not called directly) so nothing the board sends in the
        # meantime - e.g. the color-reset escape right after "Exiting
        # configuration menu" - is silently dropped.
        if not self.stop_event.is_set():
            self.root.after(20, self._poll_queue)

    def _process_ansi(self, buf):
        """Splits buf into literal text (inserted with the current color)
        and ANSI escape sequences (applied as they're found). Returns any
        trailing text that looks like the start of an escape sequence but
        isn't complete yet, to be prepended to the next chunk - cdc_menu.c's
        sequences can arrive split across two separate USB reads."""
        pos = 0
        for m in _ANSI_RE.finditer(buf):
            if m.start() > pos:
                self._insert_plain(buf[pos:m.start()])
            self._apply_escape(m.group(1), m.group(2))
            pos = m.end()
        tail = buf[pos:]
        esc_idx = tail.rfind("\x1b")
        if esc_idx != -1:
            if esc_idx > 0:
                self._insert_plain(tail[:esc_idx])
            return tail[esc_idx:]
        self._insert_plain(tail)
        return ""

    def _apply_escape(self, params_str, final):
        if final == "J":
            # cdc_menu.c only ever sends \x1b[2J (erase the whole screen,
            # as part of redrawing a menu) - clear the transcript to match.
            self.text.delete("1.0", "end")
            return
        if final == "H":
            return  # cursor-home - no-op; this view only ever appends
        if final != "m":
            return  # anything else this firmware might someday send - ignore, don't show raw

        params = [int(p) for p in params_str.split(";") if p != ""]
        i = 0
        while i < len(params):
            code = params[i]
            if code == 0:
                self._cur_fg = None
                self._cur_bg = None
                i += 1
            elif code == 38 and i + 2 < len(params) and params[i + 1] == 5:
                self._cur_fg = _xterm256_to_hex(params[i + 2])
                i += 3
            elif code == 48 and i + 2 < len(params) and params[i + 1] == 5:
                self._cur_bg = _xterm256_to_hex(params[i + 2])
                i += 3
            else:
                i += 1  # unsupported SGR code - skip it rather than misparse

    def _tag_for_current_color(self):
        fg = self._cur_fg or FG_COLOR
        bg = self._cur_bg or BG_COLOR
        name = f"c_{fg.lstrip('#')}_{bg.lstrip('#')}"
        if name not in self._known_tags:
            self.text.tag_configure(name, foreground=fg, background=bg)
            self._known_tags.add(name)
        return name

    def _insert_plain(self, text):
        if not text:
            return
        self.text.insert("end", text, self._tag_for_current_color())
        self.text.see("end")

        # Checked against the full old-tail-plus-new-text BEFORE trimming -
        # trimming first (to a fixed window) could slice a marker itself
        # off the front whenever leftover tail content pushes it past that
        # window. That's exactly what was happening to "Exiting
        # configuration menu..." (itself nearly as long as the old fixed
        # 64-char window, so almost any prior tail content pushed its start
        # out of range) while "Restarting..." - short, and always at the
        # end of its own message - happened to survive by luck.
        combined = self._tail + text
        if not self.closing:
            for marker, extra, delay_ms in _CLOSE_TRIGGERS:
                if marker in combined:
                    self.closing = True
                    if extra:
                        self.text.insert("end", extra, self._tag_for_current_color())
                        self.text.see("end")
                    self.root.after(delay_ms, self.on_close)
                    break
        # Keep only enough of a tail to bridge a marker split across two
        # reads - one byte short of the longest marker is always enough.
        self._tail = combined[-(_MAX_TRIGGER_LEN - 1):]

    def _show_disconnected(self):
        self.text.insert("end", "\n*** Disconnected ***\n")
        self.text.see("end")
        self.text.unbind("<Key>")

    def on_key(self, event):
        ch = event.char
        if ch:
            try:
                self.ser.write(ch.encode("utf-8", errors="ignore"))
            except (serial.SerialException, OSError):
                self._show_disconnected()
        return "break"  # never let Tkinter insert the keystroke itself

    def on_close(self):
        self.stop_event.set()
        try:
            self.ser.close()
        except (serial.SerialException, OSError):
            pass
        self.root.destroy()


def main():
    port = find_port()
    if port is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            APP_NAME,
            f"No MODplateR1 Config port found (VID={VID:04X}, PID={PID:04X}). "
            "Is a board plugged in over USB?",
        )
        return

    ser = serial.Serial(port, BAUD, timeout=0.05)

    root = tk.Tk()
    TerminalApp(root, ser)
    root.mainloop()


if __name__ == "__main__":
    main()
