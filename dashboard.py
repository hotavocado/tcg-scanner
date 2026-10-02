"""Dashboard data and GitHub Pages publishing.

Each run records health and alerts under STATE_DIR. The page in site/ is a
fixed shell that fetches data.json, so a publish only changes the data. A
publish is one orphan commit force-pushed to gh-pages: the branch never grows.
Publishes happen on any change, on a health flip, and as a heartbeat.
"""

import datetime as dt
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL_DIR = os.path.join(HERE, "site")
HEARTBEAT_SECS = 600
BACKOFF_MINS = (1, 2, 5, 15)
ALERT_LOG_KEEP = 50


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


class Site:
    def __init__(self, state_dir, remote=None):
        self.dir = state_dir
        self.health_file = os.path.join(state_dir, "health.json")
        self.alerts_file = os.path.join(state_dir, "alerts.json")
        self.build_dir = os.path.join(state_dir, "site")
        self.remote = remote

    def record(self, ok, sources=None, error=None, alerts=()):
        """Update health after a run. Returns True when the health state flipped."""
        h = _read(self.health_file, {})
        was_ok = h.get("consecutive_failures", 0) == 0
        now = _stamp(_now())
        h["last_run"] = now
        if ok:
            h["last_success"] = now
            h["consecutive_failures"] = 0
            h["last_error"] = None
            h["sources"] = sources or {}
        else:
            h["consecutive_failures"] = h.get("consecutive_failures", 0) + 1
            h["last_error"] = (error or "unknown error")[:300]
        h["interval_mins"] = BACKOFF_MINS[min(h["consecutive_failures"], len(BACKOFF_MINS) - 1)]
        _write(self.health_file, h)
        if alerts:
            log = _read(self.alerts_file, [])
            log = [
                {"ts": now, "kind": k, "source": p["source"], "id": p["id"], "name": p["name"],
                 "price": p.get("price"), "url": p["url"]}
                for k, p in alerts
            ] + log
            _write(self.alerts_file, log[:ALERT_LOG_KEEP])
        return was_ok != ok

    def failures(self):
        return _read(self.health_file, {}).get("consecutive_failures", 0)

    def since_last_run(self):
        last = _read(self.health_file, {}).get("last_run")
        if not last:
            return None
        return (_now() - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()

    def due(self, force=False):
        h = _read(self.health_file, {})
        last = h.get("last_publish")
        if force or not last:
            return True
        age = (_now() - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
        return age >= HEARTBEAT_SECS

    def data(self, state):
        h = _read(self.health_file, {})
        h.pop("last_publish", None)
        products = sorted(state.values(), key=lambda p: p.get("last_change") or "", reverse=True)
        return {
            "generated_at": _stamp(_now()),
            "heartbeat_secs": HEARTBEAT_SECS,
            "health": h,
            "products": products,
            "alerts": _read(self.alerts_file, []),
        }

    def publish(self, state):
        """Build site/ + data.json and force-push it as one orphan commit."""
        if not self.remote:
            return False
        if os.path.isdir(self.build_dir):
            shutil.rmtree(self.build_dir)
        shutil.copytree(SHELL_DIR, self.build_dir)
        _write(os.path.join(self.build_dir, "data.json"), self.data(state))
        open(os.path.join(self.build_dir, ".nojekyll"), "w").close()
        git = ["git", "-C", self.build_dir, "-c", "user.name=tcg-scanner", "-c", "user.email=alyssa@agents.local"]
        for cmd in (
            ["init", "-q", "-b", "gh-pages"],
            ["add", "-A"],
            ["commit", "-q", "-m", f"dashboard {_stamp(_now())}"],
            ["push", "-q", "-f", self.remote, "gh-pages"],
        ):
            subprocess.run(git + cmd, check=True, capture_output=True, timeout=60)
        h = _read(self.health_file, {})
        h["last_publish"] = _stamp(_now())
        _write(self.health_file, h)
        return True
