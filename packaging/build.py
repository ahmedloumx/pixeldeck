"""Build PixelDeck's Windows installer.

  py -3 -m pip install pyinstaller pywinpty      (once, in the Python that runs this)
  install Inno Setup 6                           (once; or set ISCC to its ISCC.exe)
  py -3 packaging/build.py [--app-only]

Steps: write build-info.json and the icon, build the app folder with PyInstaller, then
Setup.exe with Inno Setup. Writes dist/PixelDeck-Setup-<version>-x64.exe and prints its
SHA-256. --app-only stops after the app folder (build/windows/dist/PixelDeck).

The version is the tag on this commit (v1.2.0 gives 1.2.0), else VERSION in version.py.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "build" / "windows"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))


def git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def version_and_build():
    import version
    tag = git("describe", "--tags", "--exact-match", "--match", "v[0-9]*")
    commit = git("rev-parse", "--short=12", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return (tag[1:] if tag else version.VERSION), commit + ("-dirty" if dirty else "")


def iscc():
    named = os.environ.get("ISCC")
    candidates = [named] if named else []
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if base:
            candidates += [str(Path(base) / "Programs" / "Inno Setup 6" / "ISCC.exe"), str(Path(base) / "Inno Setup 6" / "ISCC.exe")]
    found = [c for c in candidates if c and Path(c).is_file()] + [shutil.which("iscc")]
    return next((c for c in found if c), None)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-only", action="store_true", help="build the app folder, not Setup.exe")
    args = parser.parse_args()
    if sys.platform != "win32":
        sys.exit("This builds the Windows installer; run it on Windows.")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(f"PyInstaller is missing: {sys.executable} -m pip install pyinstaller")
    try:
        import winpty  # noqa: F401
    except ImportError:
        sys.exit(f"pywinpty is missing (the terminal panel needs it): {sys.executable} -m pip install pywinpty")
    compiler = None if args.app_only else iscc()
    if not args.app_only and not compiler:
        sys.exit("Inno Setup 6 is missing: install it from https://jrsoftware.org/isinfo.php, or set ISCC")

    app_version, build = version_and_build()
    print(f"PixelDeck {app_version} (build {build})", flush=True)
    WORK.mkdir(parents=True, exist_ok=True)
    info = WORK / "build-info.json"
    info.write_text(json.dumps({"version": app_version, "build": build}), encoding="utf-8")
    icon = WORK / "pixeldeck.ico"
    subprocess.run([sys.executable, str(ROOT / "packaging" / "make_ico.py"), str(ROOT / "static" / "app-icon.svg"), str(icon)], check=True)

    env = {**os.environ, "PIXELDECK_BUILD_INFO": str(info), "PIXELDECK_ICON": str(icon)}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "work"),
                    str(ROOT / "packaging" / "pixeldeck.spec")], check=True, env=env, cwd=str(ROOT))
    app = WORK / "dist" / "PixelDeck"
    print(f"App folder: {app}", flush=True)
    if args.app_only:
        return

    DIST.mkdir(exist_ok=True)
    subprocess.run([compiler, "/Q", f"/DAppVersion={app_version}", f"/DSourceDir={app}", f"/DOutputDir={DIST}",
                    f"/DIconFile={icon}", str(ROOT / "packaging" / "windows" / "pixeldeck.iss")], check=True)
    setup = DIST / f"PixelDeck-Setup-{app_version}-x64.exe"
    print(f"Installer: {setup}\nSize: {setup.stat().st_size} bytes\nSHA-256: {sha256(setup)}")


if __name__ == "__main__":
    main()
