"""请求状态：TTL 过期、待处理上限与单次消费后的 410。"""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from sec_gate import httpapi
from sec_gate.httpapi import Handler
from sec_gate.store import Store


class StoreTest(unittest.TestCase):
    def test_pending_request_expires_after_ttl(self):
        store = Store()
        rid = store.new(["echo", "hi"], "", ["DEMO"], False, 60)
        self.assertEqual(store.get(rid)["state"], "pending")
        store.get(rid)["expires"] = time.time() - 1
        store.sweep()
        self.assertEqual(store.get(rid)["state"], "expired")

    def test_sweep_drops_long_finished_items(self):
        store = Store()
        rid = store.new(["echo", "hi"], "", ["DEMO"], False, 60)
        item = store.get(rid)
        item["state"] = "ran"
        item["expires"] = time.time() - 3601
        store.sweep()
        self.assertIsNone(store.get(rid))

    def test_pending_cap(self):
        store = Store()
        for _ in range(8):
            store.new(["echo", "hi"], "", ["DEMO"], False, 60)
        with self.assertRaises(RuntimeError):
            store.new(["echo", "hi"], "", ["DEMO"], False, 60)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _QuietHandler(Handler):
    def log_message(self, fmt, *args):
        pass


class SingleUseHttpTest(unittest.TestCase):
    """单次消费：同一 id 第二次提交必须 410。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_tokenfile = httpapi.TOKENFILE
        tokenfile = Path(self._tmp.name) / "api.token"
        tokenfile.write_text("test-token", encoding="utf-8")
        httpapi.TOKENFILE = tokenfile
        self.token = "test-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        httpapi.TOKENFILE = self._orig_tokenfile
        self._tmp.cleanup()

    def _post_json(self, path, payload, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Sec-Token"] = self.token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    def _post_form(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
        else:
            code = resp.status
            resp.close()
        return code

    def test_replay_returns_410(self):
        created = self._post_json(
            "/api/request",
            {"argv": ["true"], "envs": ["DEMO"], "reason": "test", "ttl": 60},
        )
        url = f"/r/{created['id']}"
        first = self._post_form(url, {"decision": "run", "v_DEMO": "sentinel"})
        self.assertEqual(first, 303)
        second = self._post_form(url, {"decision": "run", "v_DEMO": "sentinel"})
        self.assertEqual(second, 410)


if __name__ == "__main__":
    unittest.main()