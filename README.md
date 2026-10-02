# MODconfig

Tools for reaching MODplateR1's USB CDC configuration menu (the same
interactive menu the firmware's `cdc_menu.c` serves, and that the board's
web config page mirrors) without having to look up a COM port by hand.

- **MODconfigGUI.py** - a small terminal-emulator window (Tkinter) that
  renders the menu's actual ANSI colors. This is what gets packaged into
  the standalone `MODconfig` executable below.
- **MODconfig.py** - a plain console version of the same thing, for
  scripting or headless use.

Both auto-detect the board's config CDC port by VID/PID and interface
identity (the board also exposes a second, debug-only CDC port sharing the
same VID/PID - see the comments in `find_port()` for how the two are told
apart), auto-open the menu on connect, and close themselves once the board
reports the session ending (exit, save-and-restart, or restore-defaults).

## Running from source

Requires Python 3 and [pyserial](https://pypi.org/project/pyserial/):

```
pip install pyserial
python MODconfigGUI.py     # GUI
python MODconfig.py        # console
```

tkinter ships with Python on Windows and macOS; on Linux you may need to
install it separately (e.g. `sudo apt install python3-tk` on
Debian/Ubuntu).

## Standalone executables

Every push to `main` builds a standalone `MODconfig` executable for
Windows, macOS and Linux via GitHub Actions (see
`.github/workflows/build.yml`) - download them from that run's **Artifacts**
section. Pushing a tag like `v1.0.0` additionally attaches all three to a
GitHub Release.

PyInstaller can't cross-compile, so each platform's build actually runs on
that platform's own GitHub-hosted runner - there's no Windows-only way to
produce the macOS/Linux builds, which is the whole reason this is CI-driven
rather than built locally.

To build one yourself on a given platform:

```
pip install -r requirements.txt
pyinstaller --onefile --windowed --name MODconfig --icon assets/app_icon.ico MODconfigGUI.py    # Windows
pyinstaller --onefile --windowed --name MODconfig --icon assets/app_icon.icns MODconfigGUI.py   # macOS
pyinstaller --onefile --windowed --name MODconfig MODconfigGUI.py                               # Linux (no --icon support)
```
