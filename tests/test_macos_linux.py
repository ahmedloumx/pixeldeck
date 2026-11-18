"""The macOS and Linux packages, tested by pretending: the login shell's PATH, the Keychain and
keyring stores, the AppImage's server start, the app menu entry and the command, and the
AppDir the build makes. None of this ran on a real Mac or Linux desktop here."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_HOME = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
_ENV = _HOME / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
os.environ["PIXELDECK_CREDENTIALS"] = "off"  # nor a token from the credential store
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "JIRA_TOKEN"):
    os.environ.pop(_key, None)

import account  # noqa: E402
import config  # noqa: E402
import credentials  # noqa: E402
import desktop  # noqa: E402
import pixeldeck  # noqa: E402
import shellpath  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class Runner:
    """Stands in for subprocess.run: records each call, answers from a list."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, argv, input=None, **kwargs):
        self.calls.append((argv, input))
        answer = self.answers.pop(0) if self.answers else done()
        if isinstance(answer, Exception):
            raise answer
        return answer


class LoginPath(unittest.TestCase):
    def test_reads_between_the_marks_whatever_the_rc_files_print(self):
        run = Runner(done(f"Welcome!\n{shellpath.MARK}/opt/homebrew/bin:/usr/bin{shellpath.MARK}"))
        self.assertEqual(shellpath.login_path("/bin/zsh", run=run), ["/opt/homebrew/bin", "/usr/bin"])
        self.assertEqual(run.calls[0][0][:4], ["/bin/zsh", "-l", "-i", "-c"])

    def test_a_shell_that_hangs_or_fails_gives_nothing(self):
        self.assertEqual(shellpath.login_path("/bin/zsh", run=Runner(subprocess.TimeoutExpired("zsh", 5))), [])
        self.assertEqual(shellpath.login_path("/bin/zsh", run=Runner(done("no marks here"))), [])

    def test_extend_adds_only_missing_folders_that_exist_after_the_current_ones(self):
        env = {"PATH": "/usr/bin:/bin"}
        with mock.patch.object(shellpath.sys, "platform", "darwin"):
            added = shellpath.extend(env, found=["/usr/bin", "/opt/homebrew/bin", "/gone"],
                                     isdir=lambda p: p != "/gone")
        self.assertIn("/opt/homebrew/bin", added)
        self.assertNotIn("/gone", added)
        self.assertTrue(env["PATH"].startswith("/usr/bin:/bin:/opt/homebrew/bin"))

    def test_the_shell_is_asked_only_when_claude_or_git_is_missing(self):
        with mock.patch.object(shellpath.sys, "platform", "linux"), mock.patch.object(shellpath, "extend") as extend:
            self.assertIsNone(shellpath.extend_in_background(which=lambda name: f"/usr/bin/{name}"))
            thread = shellpath.extend_in_background(which=lambda name: None if name == "claude" else "/usr/bin/git")
            thread.join(5)
        self.assertEqual(extend.call_count, 1)

    def test_windows_is_left_alone(self):
        env = {"PATH": "C:\\x"}
        with mock.patch.object(shellpath.sys, "platform", "win32"):
            self.assertEqual(shellpath.extend(env, found=["/opt/homebrew/bin"]), [])
        self.assertEqual(env, {"PATH": "C:\\x"})


class Keychain(unittest.TestCase):
    def test_get_found_and_not_found(self):
        store = credentials.MacStore("PixelDeckTest", run=Runner(done("tok\n"), done("", 44)))
        self.assertEqual(store.get("BITBUCKET_TOKEN"), "tok")
        self.assertIsNone(store.get("JIRA_TOKEN"))

    def test_the_token_goes_on_standard_input_not_the_command_line(self):
        run = Runner(done())
        credentials.MacStore("PixelDeckTest", run=run).set("BITBUCKET_TOKEN", "ATATT-secret")
        argv, stdin = run.calls[0]
        self.assertEqual(argv, ["security", "-i"])
        self.assertNotIn("ATATT-secret", " ".join(argv))
        self.assertIn('-w "ATATT-secret"', stdin)

    def test_a_refusal_is_an_error_and_odd_characters_are_refused(self):
        with self.assertRaises(credentials.StoreError):
            credentials.MacStore("T", run=Runner(done(stderr="SecKeychainItemCreate: denied"))).set("K", "v")
        with self.assertRaises(credentials.StoreError):
            credentials.MacStore("T", run=Runner()).set("K", 'a"b')

    def test_delete_of_nothing_is_fine(self):
        credentials.MacStore("T", run=Runner(done(returncode=44))).delete("K")
        with self.assertRaises(credentials.StoreError):
            credentials.MacStore("T", run=Runner(done(returncode=1, stderr="no"))).delete("K")

    def test_answers_are_cached_and_fresh_asks_again(self):
        run = Runner(done("one\n"), done("two\n"))
        store = credentials.MacStore("T", run=run)
        self.assertEqual((store.get("K"), store.get("K")), ("one", "one"))
        self.assertEqual(store.get("K", fresh=True), "two")


class Keyring(unittest.TestCase):
    def test_store_reads_the_secret_from_standard_input(self):
        run = Runner(done())
        credentials.SecretServiceStore("PixelDeckTest", run=run).set("BITBUCKET_TOKEN", "secret")
        argv, stdin = run.calls[0]
        self.assertEqual(argv[:2], ["secret-tool", "store"])
        self.assertEqual(argv[-4:], ["service", "PixelDeckTest", "key", "BITBUCKET_TOKEN"])
        self.assertEqual(stdin, "secret")

    def test_lookup_and_a_missing_program(self):
        self.assertEqual(credentials.SecretServiceStore("T", run=Runner(done("tok\n"))).get("K"), "tok")
        self.assertIsNone(credentials.SecretServiceStore("T", run=Runner(FileNotFoundError())).get("K"))

    def test_the_store_each_system_gets(self):
        for platform, tool, kind in (("darwin", "security", credentials.MacStore),
                                     ("linux", "secret-tool", credentials.SecretServiceStore), ("linux", None, credentials.Store)):
            with mock.patch.dict(os.environ, {"PIXELDECK_CREDENTIALS": f"Test-{platform}-{tool}"}), \
                    mock.patch.object(credentials.sys, "platform", platform), \
                    mock.patch.object(credentials.shutil, "which", lambda name, tool=tool: f"/usr/bin/{name}" if name == tool else None):
                self.assertIs(type(credentials.store()), kind)


class SavingToAStoreThatFails(unittest.TestCase):
    def setUp(self):
        self.path = _HOME / f"{self.id()}.env"
        self.path.write_text("")
        env = mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def save_with(self, store):
        with mock.patch.object(credentials, "store", lambda: store):
            account.save({"token": "secret", "workspace": "acme"})
        return config.parse_env(self.path.read_text()).get("BITBUCKET_TOKEN")

    def test_a_refusal_keeps_the_token_in_the_file(self):
        store = credentials.SecretServiceStore("T", run=Runner(done(returncode=1, stderr="locked")))
        self.assertEqual(self.save_with(store), "secret")

    def test_a_store_that_does_not_give_it_back_keeps_it_in_the_file(self):
        store = credentials.SecretServiceStore("T", run=Runner(done(), done("", 1)))
        self.assertEqual(self.save_with(store), "secret")

    def test_a_store_that_gives_it_back_takes_it_off_the_file(self):
        store = credentials.SecretServiceStore("T", run=Runner(done(), done("secret\n")))
        self.assertIsNone(self.save_with(store))


class AppImage(unittest.TestCase):
    def pretend(self, executable, appdir="/tmp/.mount_Pixel", image="/home/me/PixelDeck.AppImage"):
        return [mock.patch.object(config, "FROZEN", True), mock.patch.object(config.sys, "platform", "linux"),
                mock.patch.object(config.sys, "executable", executable),
                mock.patch.dict(os.environ, {"APPIMAGE": image, "APPDIR": appdir})]

    def run_with(self, patches, fn):
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_believed_only_for_a_program_inside_its_mount(self):
        mine = self.run_with(self.pretend("/tmp/.mount_Pixel/usr/lib/pixeldeck/pixeldeck"), config.appimage)
        self.assertEqual(mine, Path("/home/me/PixelDeck.AppImage"))
        inherited = self.run_with(self.pretend("/home/me/PixelDeck/pixeldeck"), config.appimage)
        self.assertIsNone(inherited)

    def test_the_server_starts_through_the_appimage_file(self):
        argv = self.run_with(self.pretend("/tmp/.mount_Pixel/usr/lib/pixeldeck/pixeldeck"), pixeldeck.server_argv)
        self.assertEqual(argv, [str(Path("/home/me/PixelDeck.AppImage")), "--server"])


class FrozenEnvironment(unittest.TestCase):
    """What the packaged app hands the programs it starts."""

    def prepare(self, env, cafile=None, bundles=()):
        paths = mock.Mock(cafile=cafile, capath=None)
        with mock.patch.object(config, "FROZEN", True), mock.patch.object(pixeldeck, "WINDOWS", False):
            pixeldeck.prepare_environment(env, verify_paths=lambda: paths, isfile=lambda p: p in bundles)
        return env

    def test_the_library_path_from_before_comes_back(self):
        env = self.prepare({"LD_LIBRARY_PATH": "/tmp/.mount_x/usr/lib/pixeldeck/_internal", "LD_LIBRARY_PATH_ORIG": "/opt/lib"})
        self.assertEqual(env, {"LD_LIBRARY_PATH": "/opt/lib"})

    def test_the_bundle_library_path_goes_when_there_was_none(self):
        self.assertEqual(self.prepare({"LD_LIBRARY_PATH": "/tmp/.mount_x/_internal"}), {})

    def test_certificates_from_this_system_when_the_built_in_place_is_missing(self):
        fedora = "/etc/pki/tls/certs/ca-bundle.crt"
        self.assertEqual(self.prepare({}, bundles=(fedora,)), {"SSL_CERT_FILE": fedora})
        self.assertEqual(self.prepare({}, cafile="/usr/lib/ssl/cert.pem", bundles=(fedora,)), {})
        self.assertEqual(self.prepare({"SSL_CERT_FILE": "/mine.pem"}, bundles=(fedora,)), {"SSL_CERT_FILE": "/mine.pem"})

    def test_a_git_clone_is_left_alone(self):
        env = {"LD_LIBRARY_PATH": "/opt/lib"}
        pixeldeck.prepare_environment(env)
        self.assertEqual(env, {"LD_LIBRARY_PATH": "/opt/lib"})


class Desktop(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(dir=_HOME))
        self.program = PurePosixPath("/home/me/Apps/PixelDeck 1.AppImage")  # as on Linux, with a space
        patches = [mock.patch.object(desktop, "program", lambda: self.program),
                   mock.patch.object(desktop.Path, "home", lambda: self.home),
                   mock.patch.object(desktop.sys, "platform", "linux"),
                   mock.patch.object(desktop.shutil, "which", lambda name: None),
                   mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "share")})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_menu_entry_starts_this_program_and_marks_itself(self):
        desktop.add_menu()
        text = desktop.menu_path().read_text()
        self.assertIn(f'Exec="{self.program}"', text)
        self.assertIn(desktop.MARK, text)
        self.assertTrue(desktop.icon_path().is_file())
        self.assertEqual(desktop.status()["menu"]["state"], "ok")

    def test_a_moved_program_shows_as_stale_and_fix_rewrites_it(self):
        desktop.add_menu()
        self.program = PurePosixPath("/home/me/Elsewhere/PixelDeck.AppImage")
        self.assertEqual(desktop.status()["menu"]["state"], "stale")
        desktop.add_menu()
        self.assertEqual(desktop.status()["menu"]["state"], "ok")

    def test_an_entry_it_did_not_write_needs_replace_and_is_never_removed(self):
        path = desktop.menu_path()
        path.parent.mkdir(parents=True)
        path.write_text('[Desktop Entry]\nExec="/home/me/pixeldeck/run.sh"\n')
        self.assertEqual(desktop.status()["menu"], {"path": str(path), "state": "other", "starts": "/home/me/pixeldeck/run.sh"})
        with self.assertRaises(ValueError):
            desktop.add_menu()
        with self.assertRaises(ValueError):
            desktop.remove_menu()
        desktop.add_menu(replace=True)
        self.assertEqual(desktop.status()["menu"]["state"], "ok")
        desktop.remove_menu()
        self.assertFalse(path.exists())

    def test_the_command_runs_the_program_with_its_arguments(self):
        desktop.install_command()
        text = desktop.command_path().read_text()
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn(f"exec '{self.program}' \"$@\"", text)
        self.assertEqual(desktop.status()["command"]["state"], "ok")
        desktop.remove_command()
        self.assertEqual(desktop.status()["command"]["state"], "missing")

    def test_the_route(self):
        answer = desktop.route(None, None, "POST", "desktop", {}, {"action": "install-command"})
        self.assertEqual(answer["command"]["state"], "ok")
        with self.assertRaises(ValueError):
            desktop.route(None, None, "POST", "desktop", {}, {"action": "format-disk"})
        self.assertIsNone(desktop.route(None, None, "GET", "other", {}, {}))

    def test_nothing_is_offered_in_a_git_clone(self):
        with mock.patch.object(desktop, "program", lambda: None):
            self.assertEqual(desktop.status(), {"available": False})


def load_build():
    spec = importlib.util.spec_from_file_location("pixeldeck_build", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Build(unittest.TestCase):
    def test_the_appdir_layout(self):
        build = load_build()
        work = Path(tempfile.mkdtemp(dir=_HOME))
        app = work / "PixelDeck"
        (app / "_internal").mkdir(parents=True)
        (app / "pixeldeck").write_text("program")
        icon = work / "icon.png"
        icon.write_bytes(b"png")
        appdir = build.make_appdir(app, icon, work / "PixelDeck.AppDir")
        self.assertEqual((appdir / "usr" / "lib" / "pixeldeck" / "pixeldeck").read_text(), "program")
        self.assertIn('exec "$HERE/usr/lib/pixeldeck/pixeldeck" "$@"', (appdir / "AppRun").read_text())
        self.assertNotIn(b"\r\n", (appdir / "AppRun").read_bytes())
        entry = (appdir / f"{config.APP_ID}.desktop").read_text()
        self.assertIn("Exec=pixeldeck", entry)
        self.assertIn(f"StartupWMClass={config.APP_ID}", entry)
        self.assertEqual((appdir / ".DirIcon").read_bytes(), b"png")
        if os.name != "nt":
            self.assertTrue(os.access(appdir / "AppRun", os.X_OK))

    def test_the_macos_icon_set(self):
        sys.path.insert(0, str(ROOT / "packaging"))
        import make_ico
        folder = Path(tempfile.mkdtemp(dir=_HOME)) / "pixeldeck.iconset"
        make_ico.write_iconset(make_ico.load(str(ROOT / "static" / "app-icon.svg")), str(folder))
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(len(names), 10)
        self.assertIn("icon_512x512@2x.png", names)
        self.assertEqual((folder / "icon_16x16.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
