"""The packaged app: settings it writes itself, the token in the credential store, the setup
screen, the launcher's packaged mode and the icon."""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_HOME = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
_ENV = _HOME / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
os.environ["PIXELDECK_CREDENTIALS"] = "off"  # nor a token from the credential store
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "PIXELDECK_WORKSPACE_ROOT", "JIRA_TOKEN"):
    os.environ.pop(_key, None)

import account  # noqa: E402
import config  # noqa: E402
import credentials  # noqa: E402
import pixeldeck  # noqa: E402
import version  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class FakeStore(credentials.Store):
    name = "Test Store"

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class SettingsFile(unittest.TestCase):
    def setUp(self):
        self.path = _HOME / f"{self.id()}.env"
        patcher = mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_keeps_comments_and_other_keys(self):
        self.path.write_text("# my settings\nBITBUCKET_WORKSPACE=old\nexport JIRA_SITE=https://a.atlassian.net\n\nTICKET_PREFIXES=ABC\n")
        config.save({"BITBUCKET_WORKSPACE": "acme", "PIXELDECK_WORKSPACE_ROOT": "C:\\Users\\Jo Doe\\src"})
        text = self.path.read_text()
        self.assertTrue(text.startswith("# my settings\nBITBUCKET_WORKSPACE=acme\nexport JIRA_SITE="))
        self.assertIn("TICKET_PREFIXES=ABC", text)
        self.assertEqual(config.parse_env(text)["PIXELDECK_WORKSPACE_ROOT"], "C:\\Users\\Jo Doe\\src")

    def test_save_removes_empty_values_and_a_repeated_key(self):
        self.path.write_text("BITBUCKET_TOKEN=a\nBITBUCKET_EMAIL=me@x.com\nBITBUCKET_TOKEN=b\n")
        config.save({"BITBUCKET_TOKEN": "", "BITBUCKET_EMAIL": "you@x.com"})
        self.assertEqual(config.parse_env(self.path.read_text()), {"BITBUCKET_EMAIL": "you@x.com"})

    def test_save_quotes_what_the_parser_would_cut(self):
        for value in ("tok en", "a#b", 'say "hi"', " padded"):
            config.save({"JIRA_TOKEN": value})
            self.assertEqual(config.parse_env(self.path.read_text())["JIRA_TOKEN"], value)

    def test_save_refuses_a_line_break_or_a_bad_key(self):
        with self.assertRaises(ValueError):
            config.save({"JIRA_TOKEN": "a\nBITBUCKET_WORKSPACE=evil"})
        with self.assertRaises(ValueError):
            config.save({"BAD KEY": "x"})

    def test_a_new_value_is_read_at_once(self):
        config.save({"BITBUCKET_WORKSPACE": "first"})
        self.assertEqual(config.get("BITBUCKET_WORKSPACE"), "first")
        config.save({"BITBUCKET_WORKSPACE": "second"})
        self.assertEqual(config.get("BITBUCKET_WORKSPACE"), "second")


class Lookup(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(credentials, "store", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = _HOME / f"{self.id()}.env"
        self.path.write_text("")
        env = mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_the_environment_then_the_file_then_the_store(self):
        self.store.set("BITBUCKET_TOKEN", "from-store")
        self.assertEqual(config.get("BITBUCKET_TOKEN"), "from-store")
        config.save({"BITBUCKET_TOKEN": "from-file"})
        self.assertEqual(config.get("BITBUCKET_TOKEN"), "from-file")
        with mock.patch.dict(os.environ, {"BITBUCKET_TOKEN": "from-env"}):
            self.assertEqual(config.get("BITBUCKET_TOKEN"), "from-env")

    def test_only_the_tokens_come_from_the_store(self):
        self.store.set("BITBUCKET_WORKSPACE", "from-store")
        self.assertEqual(config.get("BITBUCKET_WORKSPACE"), "")


class EnvPath(unittest.TestCase):
    def test_a_named_file_wins(self):
        self.assertEqual(config.env_path(), Path(os.environ["PIXELDECK_ENV"]))

    def test_the_packaged_app_reads_the_config_folder(self):
        with mock.patch.dict(os.environ, {"PIXELDECK_CONFIG_DIR": str(_HOME / "cfg")}), \
                mock.patch.object(config, "FROZEN", True):
            os.environ.pop("PIXELDECK_ENV")
            try:
                self.assertEqual(config.env_path(), _HOME / "cfg" / ".env")
            finally:
                os.environ["PIXELDECK_ENV"] = str(_ENV)

    def test_a_clone_keeps_its_env_and_without_one_uses_the_config_folder(self):
        clone = _HOME / "clone"
        clone.mkdir(exist_ok=True)
        with mock.patch.dict(os.environ, {"PIXELDECK_CONFIG_DIR": str(_HOME / "cfg")}), \
                mock.patch.object(config, "APP_DIR", clone):
            os.environ.pop("PIXELDECK_ENV")
            try:
                self.assertEqual(config.env_path(), _HOME / "cfg" / ".env")
                (clone / ".env").write_text("")
                self.assertEqual(config.env_path(), clone / ".env")
            finally:
                os.environ["PIXELDECK_ENV"] = str(_ENV)

    def test_the_packaged_app_defaults_the_clones_to_home(self):
        with mock.patch.object(config, "FROZEN", True):
            self.assertEqual(config.workspace_root(), Path.home())
        self.assertEqual(config.workspace_root(), config.APP_DIR.parent)


@unittest.skipUnless(sys.platform == "win32", "Credential Manager is Windows only")
class WindowsStore(unittest.TestCase):
    def test_set_get_delete(self):
        store = credentials.WindowsStore(f"PixelDeckTest-{os.getpid()}")
        self.addCleanup(store.delete, "BITBUCKET_TOKEN")
        self.assertIsNone(store.get("BITBUCKET_TOKEN"))
        store.set("BITBUCKET_TOKEN", "ATATT-test-é")
        self.assertEqual(store.get("BITBUCKET_TOKEN"), "ATATT-test-é")
        store.delete("BITBUCKET_TOKEN")
        self.assertIsNone(store.get("BITBUCKET_TOKEN"))
        store.delete("BITBUCKET_TOKEN")  # already gone: no error

    def test_off_means_no_store(self):
        self.assertIsNone(credentials.store().name)


class FakeClient:
    def __init__(self, answers):
        self.answers, self.paths = answers, []

    def __call__(self, workspace, login=None):
        self.workspace, self.login = workspace, login
        return self

    def get(self, path, params=None):
        self.paths.append(path)
        for prefix, answer in self.answers.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {"values": []}


class Setup(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(credentials, "store", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = _HOME / f"{self.id()}.env"
        self.path.write_text("")
        env = mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_a_first_start_needs_setup(self):
        state = account.status()
        self.assertTrue(state["needed"])
        self.assertEqual((state["token"], state["store"]), (None, "Test Store"))

    def test_check_names_the_missing_scope(self):
        client = FakeClient({"/user": {"uuid": "{me}", "display_name": "Me"},
                             "/repositories/": BitbucketError(403, "lacks scopes", {"required": ["repository"], "granted": ["account"]})})
        answer = account.check({"email": "me@x.com", "token": "t", "workspace": "acme"}, client_factory=client)
        self.assertEqual(client.login, ("me@x.com", "t"))
        by_label = {r["label"]: r for r in answer["results"]}
        self.assertFalse(answer["ok"])
        self.assertEqual(by_label["Repositories"]["hint"], "add read:repository:bitbucket to the token")
        self.assertTrue(by_label["Pull requests"]["ok"])
        self.assertIn("/workspaces/acme/pullrequests/{me}", client.paths)

    def test_check_stops_at_a_wrong_token(self):
        client = FakeClient({"/user": BitbucketError(401, "Unauthorized")})
        answer = account.check({"email": "me@x.com", "token": "t", "workspace": "acme"}, client_factory=client)
        self.assertEqual([(r["label"], r["hint"]) for r in answer["results"]], [("Your user", "the email or the token is wrong")])
        self.assertEqual(client.paths, ["/user"])

    def test_check_passes(self):
        client = FakeClient({"/user": {"uuid": "{me}", "display_name": "Me"}})
        answer = account.check({"token": "t", "workspace": "acme"}, client_factory=client)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["me"], "Me")

    def test_save_puts_the_token_in_the_store_not_the_file(self):
        folder = tempfile.mkdtemp(dir=_HOME)
        state = account.save({"email": "me@x.com", "token": "secret", "workspace": "acme", "root": folder})
        self.assertNotIn("secret", self.path.read_text())
        self.assertEqual(self.store.values, {"BITBUCKET_TOKEN": "secret"})
        self.assertEqual((state["needed"], state["token"], state["root"]), (False, "store", folder))

    def test_save_without_a_token_keeps_it_and_moves_it_off_the_file(self):
        self.path.write_text("BITBUCKET_TOKEN=old\n")
        account.save({"workspace": "acme"})
        self.assertEqual(self.store.values, {"BITBUCKET_TOKEN": "old"})
        self.assertNotIn("old", self.path.read_text())

    def test_without_a_store_the_token_goes_in_the_file(self):
        with mock.patch.object(credentials, "store", lambda: credentials.Store()):
            account.save({"token": "secret", "workspace": "acme"})
        self.assertEqual(config.parse_env(self.path.read_text())["BITBUCKET_TOKEN"], "secret")

    def test_save_refuses_bad_values(self):
        for body, words in (({"token": "t", "workspace": "a/b"}, "workspace"), ({"token": "t", "workspace": "acme", "root": str(_HOME / "nope")}, "No such folder"),
                            ({"workspace": "acme"}, "Paste an API token"), ({"token": "a b", "workspace": "acme"}, "no spaces")):
            with self.assertRaises(ValueError) as caught:
                account.save(body)
            self.assertIn(words, str(caught.exception))

    def test_import_copies_a_clone_env(self):
        clone = Path(tempfile.mkdtemp(dir=_HOME))
        (clone / ".env").write_text("BITBUCKET_TOKEN=tok\nBITBUCKET_WORKSPACE=acme\nJIRA_TOKEN=jt\nTICKET_PREFIXES=ABC\n")
        state = account.import_env({"path": str(clone)})
        self.assertEqual(state["imported"], ["BITBUCKET_TOKEN", "BITBUCKET_WORKSPACE", "JIRA_TOKEN", "TICKET_PREFIXES"])
        self.assertEqual(self.store.values, {"BITBUCKET_TOKEN": "tok", "JIRA_TOKEN": "jt"})
        self.assertEqual(config.parse_env(self.path.read_text()), {"BITBUCKET_WORKSPACE": "acme", "TICKET_PREFIXES": "ABC"})
        with self.assertRaises(ValueError):
            account.import_env({"path": str(self.path)})

    def test_the_route_moves_the_engine_to_the_new_clones_folder(self):
        engine = mock.Mock(root=Path("old"))
        folder = tempfile.mkdtemp(dir=_HOME)
        account.route(None, engine, "POST", "setup", {}, {"token": "t", "workspace": "acme", "root": folder})
        self.assertEqual(engine.root, Path(folder))
        self.assertIsNone(account.route(None, engine, "GET", "nothing", {}, {}))


class PackagedLauncher(unittest.TestCase):
    def test_the_server_is_the_program_itself(self):
        exe = _HOME / "app" / "pixeldeckw.exe"
        exe.parent.mkdir(exist_ok=True)
        (exe.parent / "pixeldeck.exe").write_text("")
        with mock.patch.object(config, "FROZEN", True), mock.patch.object(sys, "executable", str(exe)):
            self.assertEqual(pixeldeck.server_argv(), [str(exe.parent / "pixeldeck.exe"), "--server"])
        self.assertEqual(pixeldeck.server_argv()[1:], [str(pixeldeck.HERE / "server.py"), "--port", str(config.port())])

    def test_stop_if_idle(self):
        for info, code, stopped in ((None, 0, False), ({"terminals": 0, "proposals": 0}, 0, True),
                                    ({"terminals": 1, "proposals": 0}, 5, False), ({"ok": True}, 5, False)):
            with mock.patch.object(pixeldeck, "health", return_value=info), \
                    mock.patch.object(pixeldeck, "stop_server") as stop, mock.patch.object(pixeldeck, "tell"):
                self.assertEqual(pixeldeck.stop_if_idle(), code, info)
                self.assertEqual(stop.called, stopped, info)

    def test_the_build_info_names_the_packaged_version(self):
        bundle = Path(tempfile.mkdtemp(dir=_HOME))
        (bundle / "build-info.json").write_text(json.dumps({"version": "1.4.0", "build": "abc123"}))
        with mock.patch.object(config, "FROZEN", True), mock.patch.object(config, "APP_DIR", bundle):
            self.assertEqual(version._baked(), {"version": "1.4.0", "build": "abc123"})
        self.assertEqual(version._baked(), {})  # a source run hashes its files instead


class Icon(unittest.TestCase):
    def test_the_ico_holds_every_size_as_png(self):
        out = _HOME / "pixeldeck.ico"
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make_ico.py"), str(ROOT / "static" / "app-icon.svg"), str(out)], check=True)
        data = out.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 6))
        for i in range(count):
            width, _, _, _, _, _, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i: 22 + 16 * i])
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">I", data[offset + 16:offset + 20])[0], width or 256)


if __name__ == "__main__":
    unittest.main()
