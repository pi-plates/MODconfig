#!/usr/bin/env python3
"""Finds the MODplateR1's USB CDC console (by VID:PID) and opens an
interactive terminal session to it, so you don't have to look up the COM
port manually to reach the config menu.

Requires pyserial:  pip install pyserial

Usage:
    python MODconfig.py
"""

import re
import sys
import threading
import time

import serial
import serial.tools.list_ports

VID = 0x2E8A
PID = 0x1158
BAUD = 115200  # USB CDC ignores the actual value, but pyserial requires one

# CDC0/CDC1's USB interface strings (see usb_descriptors.c's STRID_CDC0/1)
# and control-interface numbers (ITF_NUM_CDC_0/1). Matched the same way
# BRIDGEplate2.py's openUSB()/_port_matches_interface() already does for its
# own, sibling board - see _port_matches_interface() below for why.
CONFIG_INTERFACE_NAME = "MODplate Config"
CONFIG_INTERFACE_NUMBER = 0
DEBUG_INTERFACE_NUMBER = 2

# Text the board's cdc_menu.c prints right before each way a session ends -
# on plain Exit, and on either restart path (both share "Restarting..."
# since cdc_menu.c's confirm() gate means the operator has just deliberately
# chosen to reboot the board). Watched for below so this script closes
# itself the moment the board announces it, instead of the operator being
# left looking at a dead session (or a confusing traceback once the USB
# port actually drops out from under it a moment later).
MARKERS = [
    (b"Exiting configuration menu", "Exited configuration menu on the board. Closing."),
    (b"Restarting...", "Board is restarting. Closing."),
]
_MAX_MARKER_LEN = max(len(marker) for marker, _ in MARKERS)


_LOCATION_INTERFACE_RE = re.compile(r"\.(\d+)$")


def _location_interface_number(p):
    """The trailing USB topology interface number from p.location (e.g.
    "1-13.2:x.2" -> 2), or None if p.location isn't populated or doesn't
    look like that. Observed on this machine: Windows only populates
    location for one of this board's two CDC ports at a time (which one
    varies) - never both - so this is also used for elimination in
    find_port(), not just a direct match."""
    if not p.location:
        return None
    m = _LOCATION_INTERFACE_RE.search(p.location)
    return int(m.group(1)) if m else None


def _port_matches_interface(p, interface_name, interface_number):
    """True if `p` (a pyserial ListPortInfo, already VID/PID-matched) looks
    like the named CDC interface, tried in order of reliability - the same
    approach already proven out in this board family's BRIDGEplate2.py
    (see its own _port_matches_interface()):

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


def reader(ser, stop_event, result):
    """Prints whatever the board sends, and sets stop_event - with a
    human-readable reason appended to `result` - once the board announces
    it's ending the session, or the port itself fails (e.g. because the
    board's reboot beat this thread to noticing "Restarting...")."""
    tail = b""
    while not stop_event.is_set():
        try:
            data = ser.read(ser.in_waiting or 1)
        except (serial.SerialException, OSError):
            result.append("Lost connection to the board. Closing.")
            stop_event.set()
            return
        if not data:
            continue
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        # Check markers against the full old-tail-plus-new-data before
        # trimming anything - trimming first (to a window sized for the
        # markers) could slice a marker itself off the end whenever a
        # read() picks up trailing text along with it in one chunk, which
        # is exactly what happens here since each is one printf() call.
        combined = tail + data
        for marker, message in MARKERS:
            if marker in combined:
                result.append(message)
                stop_event.set()
                return
        # Keep only enough of a tail to bridge a marker split across two
        # reads - one byte short of the longest marker is always enough.
        tail = combined[-(_MAX_MARKER_LEN - 1):]


def forward_keys_windows(ser, stop_event):
    import msvcrt
    while not stop_event.is_set():
        if msvcrt.kbhit():
            ser.write(msvcrt.getch())
        else:
            time.sleep(0.01)


def forward_keys_posix(ser, stop_event):
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while not stop_event.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if ready:
                ser.write(sys.stdin.read(1).encode())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main():
    device = find_port()
    if device is None:
        print(f"No CDC port found with VID:PID {VID:04X}:{PID:04X}")
        sys.exit(1)

    print(f"Connecting to {device} ...")
    ser = serial.Serial(device, BAUD, timeout=0.1)

    stop_event = threading.Event()
    result = []
    reader_thread = threading.Thread(target=reader, args=(ser, stop_event, result), daemon=True)
    reader_thread.start()

    print("Connected. Press Ctrl+C to exit.\n")

    # The board's menu only opens on an Enter keypress over CDC - since this
    # script exists specifically to reach that menu, simulate one right
    # away so it shows up without the user having to press it themselves.
    time.sleep(0.1)  # let the freshly-opened port settle before writing
    ser.write(b"\r")

    try:
        if sys.platform == "win32":
            forward_keys_windows(ser, stop_event)
        else:
            forward_keys_posix(ser, stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    if result:
        print(f"\n{result[0]}")
        sys.exit(0)
    print("\nDisconnected.")


if __name__ == "__main__":
    main()
