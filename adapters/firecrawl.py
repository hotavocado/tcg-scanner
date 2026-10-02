"""Run a script inside a Firecrawl stealth page and return what it returns.

Target and Best Buy both block this VM, so their adapters call each store's
own JSON APIs from inside a page Firecrawl loads (same origin, the page's
cookies). One call is one credit. The key is read from the file the Firecrawl
MCP launcher uses and only ever goes into a request header.
"""

import json
import os
import urllib.request

API = "https://api.firecrawl.dev/v2/scrape"
KEY_FILE = os.path.expanduser(os.environ.get("TCG_FIRECRAWL_ENV", "~/.claude/secrets/firecrawl.env"))
HERE = os.path.dirname(os.path.abspath(__file__))


def api_key():
    with open(KEY_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith("FIRECRAWL_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError(f"FIRECRAWL_API_KEY not found in {KEY_FILE}")


def run(url, script_file, script_vars=None, timeout=150):
    """Load url, run adapters/<script_file> in it, return its JSON result.
    script_vars replaces placeholder tokens in the script with JSON literals."""
    with open(os.path.join(HERE, script_file)) as f:
        script = f.read()
    for token, value in (script_vars or {}).items():
        script = script.replace(token, value)
    body = {
        "url": url,
        "proxy": "stealth",
        "location": {"country": "US"},
        "storeInCache": False,
        "formats": ["markdown"],
        "includeTags": ["#probe-none"],  # matches nothing, so no page text comes back
        "actions": [{"type": "wait", "milliseconds": 1500}, {"type": "executeJavascript", "script": script}],
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    if not d.get("success"):
        raise RuntimeError(f"firecrawl: {str(d.get('error'))[:200]}")
    return json.loads(d["data"]["actions"]["javascriptReturns"][0]["value"])
