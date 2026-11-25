# DIRIGERA for Windows

Control IKEA DIRIGERA lights from the Windows system tray. Scroll to dim,
middle-click to toggle room lights, and hover for climate readings.

The app communicates directly with your hub on the local network. It requires
no cloud login or additional automation server.

This is an MIT-licensed port of [DIRIGERA Plasma](https://github.com/alsiniz/dirigera-plasma)
by alsiniz and contributors, based on upstream commit
[`a334176`](https://github.com/alsiniz/dirigera-plasma/commit/a334176309953518c76e9b2339e4c152c99556ae).

## Windows downloads

The build targets **Windows 11 x64**.

| Download | How to use it |
|---|---|
| `DIRIGERA-Windows-<version>-x64-Setup.exe` | Recommended. Installs for your Windows account, adds a Start menu shortcut, and provides an uninstaller. It does not request administrator privileges. |
| `DIRIGERA-Windows-<version>-x64-Portable.exe` | Run the standalone executable without installing. It unpacks its bundled libraries to a temporary folder at launch. |
| `DIRIGERA-Windows-<version>-x64-Portable.zip` | Extract the entire folder and run `Dirigera.exe`. Keep the `_internal` folder alongside it. |

For the unsigned test builds, open **Actions → Windows builds**, select the
successful run, and download **dirigera-windows-x64-portable-exe** or
**dirigera-windows-x64-installer** under **Artifacts**. GitHub wraps each
artifact in a ZIP. Extract the download, then run the executable inside.

All packages include Python and Qt and use the same settings under
`%LOCALAPPDATA%\DIRIGERA\config.json`. Delete that folder to clear your saved
hub authorization and app preferences. Treat the configuration file as private.
The access token can control the whole hub, while the app limits its own
actions to approved devices.

## Connect your hub

1. Open **Hub settings** and enter the hub's local IP address or hostname.
2. Choose **Generate token**. Follow the prompt to press the physical
   action button on the hub and click **Continue**.
3. Save the connection, then open **Choose devices** and approve the bulbs and
   climate sensors you want to use.

The main window offers room and individual light controls for power, brightness,
colour temperature and live colour where supported. The Climate tab displays
approved temperature and humidity sensors.

## System tray controls

If Windows puts the icon behind the tray chevron, drag it out to the area by
the clock. Choose the controlled room and icon in **Tray settings**.

| Gesture | Action |
|---|---|
| Hover | See approved temperature and humidity readings. |
| Scroll up or down | Change the brightness of approved lights that are on in the selected room, by 5% per wheel notch. A brief overlay shows the target brightness. |
| Middle-click | Toggle the approved lights in the selected room together. |
| Left-click | Open the controls window. |
| Right-click | Open the tray menu with room selection, settings, refresh and Quit. |

Closing the window keeps the tray controls available. Use **Quit** in the tray
menu or **Ctrl+Q** in the main window to exit. **Tray settings → Input diagnostics**
shows whether the wheel hook is receiving input over the icon.

## Run from source

In PowerShell on Windows, from the repo root with Python 3.12 x64 installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Build and test

Install Inno Setup 6 and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe packaging/build_windows.py --portable-exe
```

The build writes the packages to `release\`. Pass `--iscc` with the compiler
path if it is installed in a custom location. The Windows GitHub Actions
workflow builds unsigned test artifacts. Sign the app and setup executable
before a public release using the publisher's Authenticode identity. Sign
`dist\Dirigera\Dirigera.exe`, rebuild the installer and ZIP with
`packaging/build_windows.py --package-only`, then sign the setup executable.

For release testing, use an interactive Windows 11 desktop with a DIRIGERA hub.
Install from a standard account and repeat with the portable executable on a
machine without Python. Pair from a fresh configuration. Check tray scrolling in both
directions, middle-click, left-click, right-click, the climate tooltip, and the
brightness overlay. Verify that scrolling over other icons passes through and
that scrolling over DIRIGERA leaves off and unapproved bulbs alone. Test an
Explorer restart and any mixed-DPI taskbar layout being supported. Verify that
the installer upgrade and uninstall work while the app is in the tray.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for upstream component and
license information.
