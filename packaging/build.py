"""Build PixelDeck's installer for the system this runs on.

  Windows  py -3 -m pip install pyinstaller pywinpty, and Inno Setup 6 (or set ISCC)
           -> dist/PixelDeck-Setup-<version>-x64.exe
  macOS    python3 -m pip install pyinstaller
           -> dist/PixelDeck-<version>-arm64.dmg (or -x64), with hdiutil and iconutil from macOS
  Linux    python3 -m pip install pyinstaller, and appimagetool on PATH (or set APPIMAGETOOL)
           -> dist/PixelDeck-<version>-x86_64.AppImage and dist/PixelDeck-<version>-linux-x86_64.tar.gz

  python packaging/build.py [--app-only]

Steps: write build-info.json and the icon, build the app folder with PyInstaller, then the
system's package. --app-only stops after the app folder (build/<system>/dist). It prints each
file's SHA-256. The version is the tag on this commit (v1.2.0 gives 1.2.0), else VERSION in
version.py. The macOS and Linux steps have not been run yet.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SYSTEM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
WORK = ROOT / "build" / SYSTEM
APP_ID = "io.github.ahmedloumx.PixelDeck"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

import make_ico  # noqa: E402


def git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def version_and_build():
    import version
    tag = git("describe", "--tags", "--exact-match", "--match", "v[0-9]*")
    commit = git("rev-parse", "--short=12", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return (tag[1:] if tag else version.VERSION), commit + ("-dirty" if dirty else "")


def arch():
    """x64 or arm64, as the file names say it."""
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def report(*files):
    for path in files:
        print(f"{path.name}\n  size {path.stat().st_size} bytes\n  SHA-256 {sha256(path)}")


def find_tool(env_name, names, extra=()):
    named = os.environ.get(env_name)
    candidates = ([named] if named else []) + [shutil.which(n) for n in names] + list(extra)
    return next((c for c in candidates if c and Path(c).is_file()), None)


# ---------------------------------------------------------------- icons

def icon_for_system(grid):
    """The icon file PyInstaller puts on the program: .ico, .icns, or a PNG for Linux."""
    if SYSTEM == "windows":
        out = WORK / "pixeldeck.ico"
        out.write_bytes(make_ico.ico(grid))
        return out
    if SYSTEM == "macos":
        iconset = WORK / "pixeldeck.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        make_ico.write_iconset(grid, str(iconset))
        out = WORK / "pixeldeck.icns"
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
        return out
    out = WORK / "pixeldeck.png"
    make_ico.write_png(grid, 256, str(out))
    return out


# ---------------------------------------------------------------- packages

def windows_package(app_version, app, icon):
    compiler = find_tool("ISCC", ["iscc"], [
        str(Path(base) / sub / "Inno Setup 6" / "ISCC.exe")
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")) if base
        for sub in ("Programs", "")])
    if not compiler:
        sys.exit("Inno Setup 6 is missing: install it from https://jrsoftware.org/isinfo.php, or set ISCC")
    subprocess.run([compiler, "/Q", f"/DAppVersion={app_version}", f"/DSourceDir={app}", f"/DOutputDir={DIST}",
                    f"/DIconFile={icon}", str(ROOT / "packaging" / "windows" / "pixeldeck.iss")], check=True)
    return [DIST / f"PixelDeck-Setup-{app_version}-x64.exe"]


def macos_package(app_version, app_bundle):
    """A disk image with the app and a link to Applications, to drag it onto."""
    staging = WORK / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app_bundle, staging / "PixelDeck.app", symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    out = DIST / f"PixelDeck-{app_version}-{arch()}.dmg"
    out.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "PixelDeck", "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(out)],
                   check=True)
    return [out]


APPRUN = """#!/bin/sh
# Starts PixelDeck from the AppImage's mount (written by packaging/build.py).
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/pixeldeck/pixeldeck" "$@"
"""


def desktop_entry():
    return "\n".join(["[Desktop Entry]", "Type=Application", "Name=PixelDeck",
                      "Comment=Bitbucket pull requests, review threads and Claude Code fixes",
                      "Exec=pixeldeck", "Icon=pixeldeck", "Terminal=false", "Categories=Development;",
                      f"StartupWMClass={APP_ID}", ""])


def make_appdir(app, icon, appdir):
    """The AppDir appimagetool packs: AppRun, the .desktop file and the icon at its top, the app
    folder under usr/lib/pixeldeck. Generated here, so AppRun is executable whatever the git
    checkout kept."""
    shutil.rmtree(appdir, ignore_errors=True)
    shutil.copytree(app, appdir / "usr" / "lib" / "pixeldeck", symlinks=True)
    run = appdir / "AppRun"
    run.write_text(APPRUN, encoding="utf-8", newline="\n")
    run.chmod(run.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (appdir / f"{APP_ID}.desktop").write_text(desktop_entry(), encoding="utf-8", newline="\n")
    shutil.copyfile(icon, appdir / "pixeldeck.png")
    shutil.copyfile(icon, appdir / ".DirIcon")
    return appdir


def linux_package(app_version, app, icon):
    machine = "aarch64" if arch() == "arm64" else "x86_64"
    files = []
    archive = DIST / f"PixelDeck-{app_version}-linux-{machine}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(app, arcname=f"PixelDeck-{app_version}")
    files.append(archive)
    tool = find_tool("APPIMAGETOOL", ["appimagetool", f"appimagetool-{machine}.AppImage"])
    if not tool:
        print("appimagetool not found, so no AppImage: get it from https://github.com/AppImage/appimagetool/releases "
              "and put it on PATH, or set APPIMAGETOOL to it.", flush=True)
        return files
    appdir = make_appdir(app, icon, WORK / "PixelDeck.AppDir")
    image = DIST / f"PixelDeck-{app_version}-{machine}.AppImage"
    # A recent appimagetool uses the static runtime, so the AppImage does not need libfuse2.
    subprocess.run([tool, str(appdir), str(image)], check=True, env={**os.environ, "ARCH": machine})
    files.append(image)
    return files


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-only", action="store_true", help="build the app folder, not the package")
    args = parser.parse_args()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(f"PyInstaller is missing: {sys.executable} -m pip install pyinstaller")
    if SYSTEM == "windows":
        try:
            import winpty  # noqa: F401
        except ImportError:
            sys.exit(f"pywinpty is missing (the terminal panel needs it): {sys.executable} -m pip install pywinpty")

    app_version, build = version_and_build()
    print(f"PixelDeck {app_version} (build {build}) for {SYSTEM} {arch()}", flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    info = WORK / "build-info.json"
    info.write_text(json.dumps({"version": app_version, "build": build}), encoding="utf-8")
    icon = icon_for_system(make_ico.load(str(ROOT / "static" / "app-icon.svg")))

    env = {**os.environ, "PIXELDECK_BUILD_INFO": str(info), "PIXELDECK_ICON": str(icon), "PIXELDECK_VERSION": app_version}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "work"),
                    str(ROOT / "packaging" / "pixeldeck.spec")], check=True, env=env, cwd=str(ROOT))
    app = WORK / "dist" / ("PixelDeck.app" if SYSTEM == "macos" else "PixelDeck")
    print(f"App: {app}", flush=True)
    if args.app_only:
        return

    DIST.mkdir(exist_ok=True)
    if SYSTEM == "windows":
        files = windows_package(app_version, app, icon)
    elif SYSTEM == "macos":
        files = macos_package(app_version, app)
    else:
        files = linux_package(app_version, app, icon)
    report(*files)


if __name__ == "__main__":
    main()
