"""Starting and stopping the server: one per port, a pid file that names the live one, and no
console windows for the programs it runs."""

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
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
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE"):
    os.environ.pop(_key, None)

import config  # noqa: E402
import connections  # noqa: E402
import gitscan  # noqa: E402
import server  # noqa: E402
import version  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    res = conn.getresponse()
    return res.status, res.read()


class NoConsoleWindowTest(unittest.TestCase):
    def test_git_and_version_checks_ask_for_no_window(self):
        flags = []

        def fake_run(argv, **kw):
            flags.append(kw.get("creationflags"))
            return subprocess.CompletedProcess(argv, 0, stdout="git version 2.45\n", stderr="")

        with mock.patch("subprocess.run", fake_run):
            gitscan.git(".", "--version")
            connections._versions.clear()
            connections._version(["git", "--version"])
        self.assertEqual(flags, [gitscan.NO_WINDOW, gitscan.NO_WINDOW])
        self.assertEqual(gitscan.NO_WINDOW, subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


class OneServerPerPortTest(unittest.TestCase):
    def serve(self, port=0):
        httpd = server.DeskServer(("127.0.0.1", port), server.Desk)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd

    def test_a_second_server_cannot_take_a_port_in_use(self):
        port = self.serve().server_address[1]
        with self.assertRaises(OSError):
            server.DeskServer(("127.0.0.1", port), server.Desk)

    def test_nothing_else_can_bind_it_with_reuse_either(self):
        port = self.serve().server_address[1]
        with socket.socket() as s, self.assertRaises(OSError):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))

    def test_a_burst_of_connections_waits_instead_of_being_refused(self):
        httpd = server.DeskServer(("127.0.0.1", 0), server.Desk)  # listening, not accepting yet
        self.addCleanup(httpd.server_close)
        socks = []
        self.addCleanup(lambda: [s.close() for s in socks])
        for _ in range(40):
            socks.append(socket.create_connection(httpd.server_address, timeout=2))

    def test_a_restart_gets_the_port_back_after_traffic(self):
        httpd = server.DeskServer(("127.0.0.1", 0), server.Desk)
        port = httpd.server_address[1]
        server.Desk.port = port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        for _ in range(10):  # closed connections stay in TIME_WAIT on the port for a while
            get(port, "/healthz")
        httpd.shutdown()
        httpd.server_close()
        self.serve(port)

    def test_healthz_says_which_process_answers(self):
        port = self.serve().server_address[1]
        server.Desk.port = port
        status, body = get(port, "/healthz")
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual((info["ok"], info["pid"]), (True, os.getpid()))
        self.assertEqual((info["version"], info["build"]), (version.VERSION, server.BUILD))
        self.assertEqual((info["terminals"], info["proposals"]), (0, 0))


class BuildTest(unittest.TestCase):
    def test_the_fingerprint_follows_the_python_files_only(self):
        folder = Path(tempfile.mkdtemp(prefix="pixeldeck-build-"))
        (folder / "server.py").write_text("print('one')\n")
        (folder / "static").mkdir()
        with mock.patch.object(config, "APP_DIR", folder):
            first = version.build()
            (folder / "static" / "app.js").write_text("// the page is read from disk on each request\n")
            self.assertEqual(version.build(), first)
            (folder / "server.py").write_text("print('two')\n")
            self.assertNotEqual(version.build(), first)

    def test_the_server_knows_when_the_files_moved_on(self):
        self.assertFalse(server.code_changed())
        with mock.patch.object(version, "build", lambda: "after-a-pull"):
            self.assertTrue(server.code_changed())


class LauncherTest(unittest.TestCase):
    """Real servers, started the way the launcher starts them, on a free port and in folders
    of their own, so a test never meets the PixelDeck you are running."""

    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="pixeldeck-launch-"))
        env = {"PIXELDECK_PORT": str(free_port()), "PIXELDECK_WORKSPACE_ROOT": str(folder)}
        env.update({f"PIXELDECK_{kind}_DIR": str(folder / kind.lower()) for kind in ("STATE", "CACHE", "DATA", "CONFIG")})
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        import pixeldeck
        self.pd = pixeldeck
        self.addCleanup(self._stop_quietly)

    def _stop_quietly(self):
        with mock.patch("builtins.print"):
            self.pd.stop_server()

    def pid_file(self):
        return self.pd.state_file("server.pid")

    def test_a_second_start_leaves_the_running_server_and_its_pid_file(self):
        self.pd.start_server()
        first = self.pd.health()["pid"]
        self.assertEqual(self.pid_file().read_text(), str(first))
        self.pd.start_server()  # what a too-early "not running" leads to
        self.assertEqual(self.pd.health()["pid"], first)
        self.assertEqual(self.pid_file().read_text(), str(first))

    def start_pixeldeck(self):
        """What a double-click does, without opening a window."""
        with mock.patch.object(sys, "argv", ["pixeldeck.py"]), mock.patch.object(self.pd, "open_window", lambda: "test"), \
                mock.patch("builtins.print") as said:
            self.pd.main()
        return " ".join(str(c.args[0]) for c in said.call_args_list if c.args)

    def test_a_server_running_older_code_is_restarted(self):
        self.pd.start_server()
        before = self.pd.health()["pid"]
        with mock.patch.object(self.pd.version, "build", lambda: "after-a-pull"):
            said = self.start_pixeldeck()
        self.assertIn("restarting the server", said)
        self.assertNotEqual(self.pd.health()["pid"], before)
        self.assertEqual(self.pid_file().read_text(), str(self.pd.health()["pid"]))

    def held(self, running):
        """Start PixelDeck against a server that says `running`; it must not be stopped."""
        with mock.patch.object(self.pd, "health", lambda timeout=1.0: running), \
                mock.patch.object(self.pd, "stop_server") as stop, mock.patch.object(self.pd, "start_server") as start:
            said = self.start_pixeldeck()
        stop.assert_not_called()
        start.assert_not_called()
        return said

    def test_open_terminals_hold_the_restart(self):
        said = self.held({"ok": True, "pid": 4242, "build": "before-a-pull", "terminals": 2, "proposals": 0})
        self.assertIn("open terminals", said)

    def test_an_agent_waiting_on_a_proposal_holds_the_restart(self):
        said = self.held({"ok": True, "pid": 4242, "build": "before-a-pull", "terminals": 0, "proposals": 1})
        self.assertIn("pending proposal", said)

    def test_a_server_from_before_this_check_is_not_stopped_blindly(self):
        said = self.held({"ok": True})  # what an older server's bare "ok" reads as
        self.assertIn("--stop", said)
        self.assertNotIn("has open terminals", said)

    def test_an_up_to_date_server_is_left_alone(self):
        self.pd.start_server()
        before = self.pd.health()["pid"]
        said = self.start_pixeldeck()
        self.assertNotIn("restarting", said)
        self.assertEqual(self.pd.health()["pid"], before)

    def test_stop_believes_the_server_over_a_stale_pid_file(self):
        self.pd.start_server()
        self.pid_file().write_text("999999")
        with mock.patch("builtins.print"):
            self.pd.stop_server()
        self.assertFalse(self.pd.alive())


if __name__ == "__main__":
    unittest.main()
