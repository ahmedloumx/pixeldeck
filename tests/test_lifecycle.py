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
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE"):
    os.environ.pop(_key, None)

import connections  # noqa: E402
import gitscan  # noqa: E402
import server  # noqa: E402


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
        self.assertEqual(json.loads(body), {"ok": True, "pid": os.getpid()})


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

    def test_stop_believes_the_server_over_a_stale_pid_file(self):
        self.pd.start_server()
        self.pid_file().write_text("999999")
        with mock.patch("builtins.print"):
            self.pd.stop_server()
        self.assertFalse(self.pd.alive())


if __name__ == "__main__":
    unittest.main()
