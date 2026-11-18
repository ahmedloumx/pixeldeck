# PyInstaller spec for PixelDeck: one folder, two programs sharing it.
#   pixeldeck   a console program: `pixeldeck --stop`, `pixeldeck --propose`, and the server itself
#   pixeldeckw  the same without a console (Windows only), for the Start menu
# Run it through packaging/build.py, which writes build-info.json and the icon first.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
ICON = os.environ.get("PIXELDECK_ICON") or None

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "briefs"), "briefs"),
    (str(ROOT / "licenses"), "licenses"),
    (str(ROOT / "LICENSE"), "."),
    (os.environ["PIXELDECK_BUILD_INFO"], "."),
]
binaries = []
if WINDOWS:
    # pywinpty's terminal hosts. PyInstaller bundles its DLLs but not these programs, and
    # without OpenConsole.exe a shell dies at the first key.
    import winpty
    folder = Path(winpty.__file__).parent
    binaries = [(str(folder / name), "winpty") for name in ("OpenConsole.exe", "winpty-agent.exe") if (folder / name).is_file()]

a = Analysis(
    [str(ROOT / "pixeldeck.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=["server"],  # imported inside main(), for --server
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=["tkinter", "gi"],  # app_window.py (GTK) stays a source-run feature
)
pyz = PYZ(a.pure)
programs = [EXE(pyz, a.scripts, [], exclude_binaries=True, name="pixeldeck", console=True, icon=ICON, upx=False)]
if WINDOWS:
    programs.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="pixeldeckw", console=False, icon=ICON, upx=False))
COLLECT(*programs, a.binaries, a.datas, name="PixelDeck", upx=False)
