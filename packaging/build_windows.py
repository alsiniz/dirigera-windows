"""Build a Windows x64 installer and portable builds."""

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
APP_ICON = ROOT / "assets" / "dirigera.ico"
DIST = ROOT / "dist"
RELEASE = ROOT / "release"
APP = DIST / "Dirigera"
PORTABLE_EXE = RELEASE / "portable-exe"
BUNDLE_DOCUMENTS = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md")


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


def freeze_app(name, mode, output):
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed", mode, "--noupx",
            "--name", name, "--icon", str(APP_ICON),
            "--distpath", str(output), "--workpath", str(BUILD / "pyinstaller"),
            "--specpath", str(BUILD),
            "--add-data", f"{APP_ICON}:assets",
            "--copy-metadata", "PySide6-Essentials", "--copy-metadata", "dirigera",
            str(ROOT / "app.py"),
        ],
        check=True,
        cwd=ROOT,
    )


def build_app():
    freeze_app("Dirigera", "--onedir", DIST)
    copy_licenses()
    for filename in BUNDLE_DOCUMENTS:
        shutil.copy2(ROOT / filename, APP / filename)


def check_runtime(executable):
    subprocess.run(
        [str(executable), "--check-runtime"],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        check=True,
        timeout=60,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, help="Path to the Inno Setup 6 compiler")
    parser.add_argument("--package-only", action="store_true", help="Use the existing app bundle")
    parser.add_argument("--portable-exe", action="store_true", help="Also build a standalone portable executable")
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"} or sys.maxsize <= 2**32:
        parser.error("Build on Windows with x64 Python")
    iscc = args.iscc or find_iscc()
    if iscc is None or not iscc.is_file():
        parser.error("Install Inno Setup 6 or specify the compiler with --iscc")

    sys.path.insert(0, str(ROOT))
    from version import VERSION

    portable_name = f"DIRIGERA-Windows-{VERSION}-x64-Portable"
    portable_executable = PORTABLE_EXE / f"{portable_name}.exe"
    if not args.package_only:
        BUILD.mkdir(exist_ok=True)
        build_app()
        if args.portable_exe:
            freeze_app(portable_name, "--onefile", PORTABLE_EXE)
            shutil.copytree(APP / "licenses", PORTABLE_EXE / "licenses", dirs_exist_ok=True)
            for filename in BUNDLE_DOCUMENTS:
                shutil.copy2(APP / filename, PORTABLE_EXE / filename)
    elif not (APP / "Dirigera.exe").is_file():
        parser.error("Run a full build before using --package-only")
    elif args.portable_exe and not portable_executable.is_file():
        parser.error("Run a full build with --portable-exe first")

    check_runtime(APP / "Dirigera.exe")
    if args.portable_exe:
        check_runtime(portable_executable)
    RELEASE.mkdir(exist_ok=True)
    subprocess.run(
        [str(iscc.resolve()), f"/DAppVersion={VERSION}", str(ROOT / "packaging" / "dirigera.iss")],
        check=True,
        cwd=ROOT,
    )
    shutil.make_archive(
        str(RELEASE / portable_name),
        "zip",
        root_dir=DIST,
        base_dir=APP.name,
    )
    for artifact in sorted(RELEASE.glob(f"DIRIGERA-Windows-{VERSION}-x64-*")):
        print(artifact)
    if args.portable_exe:
        print(portable_executable)


if __name__ == "__main__":
    main()
