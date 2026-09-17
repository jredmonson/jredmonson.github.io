#!/usr/bin/env python3
"""
Cheap post-merge IndexNow nudge for content merged through the GHL pipeline.
Pings with the sitemap URL only (not per-article URLs, since a CI merge
step doesn't cleanly know the final permalink without duplicating Jekyll's
own routing logic) — this matches the "low-cost nudge" pattern already used
elsewhere on this site (see [[seo-two-week-sprint]]). Reuses the same
IndexNow key/host already live in scripts/autopublish.py.

Usage: python3 scripts/ping_indexnow_sitemap.py
Never raises — a failed ping should never fail the workflow.
"""
import json
import urllib.request

INDEXNOW_KEY = "99fd7e5092a742d4bbcdcd761699f345"
INDEXNOW_HOST = "jredmonson.github.io"


def main():
    payload = {
        "host": INDEXNOW_HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://{INDEXNOW_HOST}/{INDEXNOW_KEY}.txt",
        "urlList": [f"https://{INDEXNOW_HOST}/sitemap.xml"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"IndexNow ping status: {resp.status}")
    except Exception as e:  # noqa: BLE001 - best-effort, never fail the run
        print(f"IndexNow ping failed (continuing anyway): {e}")


if __name__ == "__main__":
    main()
