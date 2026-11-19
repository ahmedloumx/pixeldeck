# PyInstaller spec for PixelDeck: one folder, the same on every system.
#   pixeldeck   the program: the window's launcher, `pixeldeck --stop`, `pixeldeck --propose`,
#               and the server itself (`pixeldeck --server`)
#   pixeldeckw  Windows only: the same without a console, for the Start menu
# macOS wraps the folder in PixelDeck.app. Run it through packaging/build.py, which writes
# build-info.json and the icon first.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
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
# On macOS the program goes in PixelDeck.app; run from a terminal it still prints, so the same
# file serves `pixeldeck --propose` (About writes a ~/.local/bin/pixeldeck that starts it).
programs = [EXE(pyz, a.scripts, [], exclude_binaries=True, name="pixeldeck", console=not MACOS, icon=ICON, upx=False,
                argv_emulation=False)]
if WINDOWS:
    programs.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="pixeldeckw", console=False, icon=ICON, upx=False))
app = COLLECT(*programs, a.binaries, a.datas, name="PixelDeck", upx=False)
if MACOS:
    VERSION = os.environ.get("PIXELDECK_VERSION", "0.0.0")
    BUNDLE(app, name="PixelDeck.app", icon=ICON, bundle_identifier="io.github.ahmedloumx.PixelDeck", version=VERSION,
           info_plist={"CFBundleName": "PixelDeck", "CFBundleDisplayName": "PixelDeck", "CFBundleShortVersionString": VERSION,
                       "NSHighResolutionCapable": True, "LSApplicationCategoryType": "public.app-category.developer-tools"})
