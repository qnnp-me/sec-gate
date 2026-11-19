"""请求状态：服务进程内的内存存储，含 TTL 与待处理上限。"""
import secrets
import threading
import time

from . import MAX_PENDING
from .risk import risk_profile


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def new(self, argv, reason, envs, stdin_mode, ttl):
        if len([i for i in self.items.values() if i["state"] == "pending"]) >= MAX_PENDING:
            raise RuntimeError("待处理请求过多，先处理或等它们过期")
        rid = secrets.token_urlsafe(24)
        now = time.time()
        with self.lock:
            self.items[rid] = {
                "id": rid, "argv": argv, "reason": reason, "envs": envs,
                "risk": risk_profile(argv),
                "stdin": stdin_mode, "created": now, "expires": now + ttl,
                "state": "pending", "used": False, "result": None,
            }
        return rid

    def get(self, rid):
        with self.lock:
            return self.items.get(rid)

    def sweep(self):
        now = time.time()
        with self.lock:
            for rid, item in list(self.items.items()):
                if item["state"] == "pending" and now > item["expires"]:
                    item["state"] = "expired"
                if item["state"] != "pending" and now > item["expires"] + 3600:
                    del self.items[rid]

    def pending(self):
        with self.lock:
            return [i for i in self.items.values() if i["state"] == "pending"]


STORE = Store()
