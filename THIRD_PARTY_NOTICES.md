# Third-party components

This project is a Windows port of [Lysnav for KDE Plasma](https://github.com/alsiniz/lysnav-plasma)
by alsiniz and contributors. The upstream MIT license is included in [LICENSE](LICENSE).

| Component | Purpose | License |
|---|---|---|
| [dirigera](https://github.com/Leggin/dirigera) | Unofficial local DIRIGERA API and device models | MIT |
| [PySide6 and Shiboken](https://code.qt.io/cgit/pyside/pyside-setup.git/) | Qt bindings for Python | LGPLv3, GPLv3 or commercial |
| [Qt](https://download.qt.io/archive/qt/) | Desktop UI and native notification-area support | LGPLv3, GPLv3 or commercial for the bundled modules |
| [Python](https://www.python.org/downloads/source/) | Runtime | PSF License and other notices |
| [PyInstaller](https://pyinstaller.org/en/stable/license.html) | Executable bundling | GPLv2 with bootloader exception |
| [Inno Setup](https://jrsoftware.org/) | Installer | Inno Setup License |

The Windows bundles include the library versions and wheel-provided license
files under `licenses/`, along with Qt’s LGPLv3 and GPLv3 license texts.
Qt publishes its [third-party notices](https://doc.qt.io/qt-6/licenses-used-in-qt.html).
Qt libraries are loaded dynamically from the `_internal/` directory in the
installer and portable ZIP. The standalone executable extracts them at launch.
To rebuild with a different compatible Qt version, install it in the build
environment and use the [build instructions](README.md#build-and-test).

IKEA and DIRIGERA are marks of their respective owners. This project is
independent of IKEA.
