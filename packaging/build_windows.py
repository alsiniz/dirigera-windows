"""Build a Windows x64 installer and a no-install ZIP from one Qt bundle."""

import argparse
from collections import deque
import importlib.metadata as metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP = DIST / "Dirigera"


def find_iscc():
    found = shutil.which("ISCC.exe")
    if found:
        return Path(found)
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(base):
            parent = Path(os.environ[base])
            for candidate in (parent / "Inno Setup 6", parent / "Programs" / "Inno Setup 6"):
                if (compiler := candidate / "ISCC.exe").is_file():
                    return compiler
    return None


def copy_licenses():
    output = APP / "licenses"
    shutil.copytree(ROOT / "packaging" / "qt-licenses", output / "Qt", dirs_exist_ok=True)
    pending = deque(Requirement(line) for line in (ROOT / "requirements.txt").read_text().splitlines())
    installed = {}
    while pending:
        requirement = pending.popleft()
        name = canonicalize_name(requirement.name)
        if name in installed or (requirement.marker and not requirement.marker.evaluate()):
            continue
        package = metadata.distribution(name)
        installed[name] = package.version
        for file in package.files or []:
            info_dir = next((part for part in file.parts if part.endswith(".dist-info")), None)
            if not info_dir:
                continue
            relative = Path(*file.parts[file.parts.index(info_dir) + 1:])
            if relative.parts[0] == "licenses" or relative.name.upper().startswith(
                ("LICENSE", "COPYING", "NOTICE")
            ):
                target = output / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file.locate(), target)
        pending.extend(Requirement(entry) for entry in package.requires or [])
    for filename in ("LICENSE.txt", "LICENSE"):
        source = Path(sys.base_prefix) / filename
        if source.is_file():
            shutil.copy2(source, output / "Python-LICENSE.txt")
            break
    (output / "versions.txt").write_text(
        f"Python=={platform.python_version()}\n"
        + "".join(f"{name}=={version}\n" for name, version in sorted(installed.items())),
        encoding="utf-8",
    )


def make_icon():
    from PySide6.QtGui import QGuiApplication, QIcon

    app = QGuiApplication([])
    app.setApplicationName("DIRIGERA icon builder")
    icon = QIcon(str(ROOT / "assets" / "dirigera.svg"))
    destination = BUILD / "dirigera.ico"
    if not icon.pixmap(128, 128).save(str(destination), "ICO"):
        raise RuntimeError("Failed to create the Windows application icon")
    return destination


def build_app():
    BUILD.mkdir(exist_ok=True)
    icon = make_icon()
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed", "--onedir", "--noupx",
            "--name", "Dirigera", "--icon", str(icon),
            "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
            "--specpath", str(BUILD),
            "--add-data", f"{ROOT / 'assets'}:assets",
            "--hidden-import", "PySide6.QtSvg",
            "--copy-metadata", "PySide6-Essentials", "--copy-metadata", "dirigera",
            str(ROOT / "app.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    copy_licenses()
    for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        shutil.copy2(ROOT / filename, APP / filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, help="Path to the Inno Setup 6 compiler")
    parser.add_argument("--package-only", action="store_true", help="Use the existing app bundle")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"} or sys.maxsize <= 2**32:
        parser.error("Build on Windows with x64 Python")
    iscc = args.iscc or find_iscc()
    if iscc is None or not iscc.is_file():
        parser.error("Install Inno Setup 6 or specify the compiler with --iscc")

    sys.path.insert(0, str(ROOT))
    from version import VERSION

    if not args.package_only:
        build_app()
    elif not (APP / "Dirigera.exe").is_file() or not (BUILD / "dirigera.ico").is_file():
        parser.error("Run a full build before using --package-only")

    RELEASE.mkdir(exist_ok=True)
    subprocess.run(
        [str(iscc.resolve()), f"/DAppVersion={VERSION}", str(ROOT / "packaging" / "dirigera.iss")],
        check=True,
        cwd=ROOT,
    )
    shutil.make_archive(
        str(RELEASE / f"DIRIGERA-Windows-{VERSION}-x64-Portable"),
        "zip",
        root_dir=DIST,
        base_dir=APP.name,
    )
    for artifact in sorted(RELEASE.glob(f"DIRIGERA-Windows-{VERSION}-x64-*")):
        print(artifact)


if __name__ == "__main__":
    main()
