"""
End-to-end tester for POST /api/tracks/{seed}/similar (DJ mode).

Measures:
  A. Latency: N sequential POSTs against warm cache, prints avg/min/max/p50/p95
     and echoes `mode_used` so you can tell if you're on the Turso fast path
     or numpy fallback.
  B. Exclusion correctness: sends a curated exclude_ids set and verifies
     zero results overlap.
  C. Exclude scale: probes how the endpoint behaves with exclude lists of
     increasing size (10, 50, 100, 200) — flags latency regressions and any
     leakage.

Auth: reads credentials from env vars.
  VIBESCAPE_BASE_URL   e.g. https://vibescape-241988497106.us-central1.run.app
  VIBESCAPE_EMAIL      login email
  VIBESCAPE_PASSWORD   login password
  (or) VIBESCAPE_TOKEN existing session_token to skip login

Usage (PowerShell):
  $env:VIBESCAPE_BASE_URL = "https://vibescape-241988497106.us-central1.run.app"
  $env:VIBESCAPE_EMAIL    = "you@example.com"
  $env:VIBESCAPE_PASSWORD = "..."
  python scripts/_test_similar_endpoint.py
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time
from typing import Any
from urllib import request as urlreq
from urllib.error import HTTPError


BASE_URL = (os.environ.get("VIBESCAPE_BASE_URL") or "").rstrip("/")
EMAIL = os.environ.get("VIBESCAPE_EMAIL")
PASSWORD = os.environ.get("VIBESCAPE_PASSWORD")
TOKEN = os.environ.get("VIBESCAPE_TOKEN")


def _http(method: str, path: str, *, token: str | None = None, body: Any = None, timeout: float = 30.0):
    url = BASE_URL + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urlreq.Request(url, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            dt_ms = (time.perf_counter() - t0) * 1000
            return resp.status, json.loads(payload) if payload else None, dt_ms
    except HTTPError as e:
        payload = e.read()
        dt_ms = (time.perf_counter() - t0) * 1000
        try:
            return e.code, json.loads(payload), dt_ms
        except Exception:
            return e.code, {"raw": payload.decode("utf-8", "replace")}, dt_ms


def login() -> str:
    if TOKEN:
        return TOKEN
    if EMAIL and PASSWORD:
        status, body, _ = _http("POST", "/api/auth/login", body={"email": EMAIL, "password": PASSWORD})
        if status != 200 or not body or "session_token" not in body:
            sys.exit(f"Login failed: status={status} body={body}")
        print(f"[auth] logged in as {body.get('display_name')!r} (user_id={body.get('user_id')})")
        return body["session_token"]
    # Fall back to shared demo guest account.
    status, body, _ = _http("POST", "/api/auth/guest", body={})
    if status != 200 or not body or "session_token" not in body:
        sys.exit(f"Guest login failed: status={status} body={body}")
    print(f"[auth] guest session as {body.get('display_name')!r} (user_id={body.get('user_id')})")
    return body["session_token"]


def fetch_library(token: str, limit: int = 500) -> list[dict]:
    status, body, _ = _http("GET", f"/api/tracks?limit={limit}", token=token)
    if status != 200 or not body:
        sys.exit(f"Failed to load library: status={status}")
    tracks = body.get("tracks") if isinstance(body, dict) else body
    if not tracks:
        sys.exit("Library is empty.")
    print(f"[lib] fetched {len(tracks)} tracks")
    return tracks


def key_of(t: dict) -> str:
    """Frontend seed key — spotify_id (backend also accepts internal id)."""
    return str(t.get("spotify_id") or (t.get("id") if t.get("id") is not None else ""))


def id_of(t: dict):
    """Internal tracks.id integer — the hot-path key for pos/neg/exclude
    lists. Sent to backend as a plain int so it skips per-key resolution."""
    tid = t.get("id")
    return int(tid) if tid is not None else None


def pctile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[k]


def summarize(label: str, times_ms: list[float]) -> None:
    print(
        f"  {label:24s} "
        f"n={len(times_ms):<3d} "
        f"avg={statistics.mean(times_ms):7.0f}ms  "
        f"p50={pctile(times_ms, 50):7.0f}  "
        f"p95={pctile(times_ms, 95):7.0f}  "
        f"min={min(times_ms):7.0f}  "
        f"max={max(times_ms):7.0f}"
    )


def post_similar(token: str, seed_key: str, body: dict) -> tuple[int, dict, float]:
    return _http(
        "POST",
        f"/api/tracks/{seed_key}/similar",
        token=token,
        body=body,
    )


def test_latency(token: str, seed_key: str, tracks: list[dict], reps: int = 8) -> None:
    print(f"\n[A] Latency — {reps} sequential POSTs, warm cache, seed={seed_key}")
    # Send internal ids (ints) — matches what the frontend now sends on the
    # hot path. Backend skips resolution entirely for int entries.
    positives = [{"id": id_of(t), "weight": 1.0} for t in tracks[:5] if id_of(t) is not None]
    excludes = [id_of(t) for t in tracks[5:55] if id_of(t) is not None]
    body = {
        "mode": "dj",
        "positive_ids": positives,
        "negative_ids": [],
        "exclude_ids": excludes,
        "limit": 8,
    }
    modes_seen = set()
    times: list[float] = []
    for i in range(reps):
        status, resp, dt = post_similar(token, seed_key, body)
        if status != 200:
            print(f"  req {i}: STATUS {status} body={resp}")
            continue
        mode = (resp or {}).get("mode_used", "?")
        modes_seen.add(mode)
        n = len((resp or {}).get("tracks") or [])
        times.append(dt)
        print(f"  req {i}: {dt:6.0f}ms  mode={mode}  n={n}")
    if times:
        summarize("latency", times)
    print(f"  modes seen: {sorted(modes_seen)}")


def test_exclusion(token: str, seed_key: str, tracks: list[dict]) -> None:
    print(f"\n[B] Exclusion correctness — seed={seed_key}")
    positives = [{"id": id_of(t), "weight": 1.0} for t in tracks[:5] if id_of(t) is not None]
    # First, get the natural top-8 with only the seed excluded.
    baseline_body = {"mode": "dj", "positive_ids": positives, "negative_ids": [],
                     "exclude_ids": [], "limit": 8}
    status, resp, _ = post_similar(token, seed_key, baseline_body)
    if status != 200:
        print(f"  baseline failed: {status} {resp}")
        return
    baseline = resp.get("tracks") or []
    baseline_ids = [id_of(t) for t in baseline if id_of(t) is not None]
    print(f"  baseline top {len(baseline)} ids: {baseline_ids}")
    if any(id_of(t) is None for t in baseline):
        print("  !! some baseline tracks are missing internal id")

    # Now exclude those exact IDs and re-fetch — none of them should re-appear.
    body = {"mode": "dj", "positive_ids": positives, "negative_ids": [],
            "exclude_ids": baseline_ids, "limit": 8}
    status, resp, _ = post_similar(token, seed_key, body)
    if status != 200:
        print(f"  exclude round failed: {status} {resp}")
        return
    after = resp.get("tracks") or []
    after_ids = [id_of(t) for t in after]
    print(f"  after excluding baseline: {after_ids}")

    excl_set = set(baseline_ids)
    violations = [k for k in after_ids if k in excl_set]
    if violations:
        print(f"  !! VIOLATIONS: {violations}")
    else:
        print(f"  OK — zero overlap with exclude set")


def test_exclude_scale(token: str, seed_key: str, tracks: list[dict]) -> None:
    print(f"\n[C] Exclude scale — sweep exclude list size, seed={seed_key}")
    positives = [{"id": id_of(t), "weight": 1.0} for t in tracks[:5] if id_of(t) is not None]
    all_ids = [id_of(t) for t in tracks if id_of(t) is not None]
    random.shuffle(all_ids)
    for size in (0, 10, 50, 100, 200, 500):
        if size > len(all_ids):
            continue
        excl = all_ids[:size]
        excl_set = set(excl)
        body = {"mode": "dj", "positive_ids": positives, "negative_ids": [],
                "exclude_ids": excl, "limit": 8}
        times: list[float] = []
        violations = 0
        mode = "?"
        for _ in range(3):
            status, resp, dt = post_similar(token, seed_key, body)
            if status != 200:
                print(f"  size={size}: STATUS {status}")
                break
            times.append(dt)
            mode = (resp or {}).get("mode_used", mode)
            for t in (resp or {}).get("tracks") or []:
                tid = id_of(t)
                if tid is not None and tid in excl_set:
                    violations += 1
        if times:
            print(
                f"  size={size:<4d} avg={statistics.mean(times):6.0f}ms  "
                f"min={min(times):6.0f}  max={max(times):6.0f}  "
                f"violations={violations}  mode={mode}"
            )


def main() -> None:
    if not BASE_URL:
        sys.exit("Set VIBESCAPE_BASE_URL.")
    print(f"[cfg] base_url={BASE_URL}")
    token = login()
    tracks = fetch_library(token, limit=500)
    # Pick a stable seed — first track with an id.
    seed = next((t for t in tracks if key_of(t)), None)
    if not seed:
        sys.exit("No usable seed track (no spotify_id/apple_id).")
    seed_key = key_of(seed)
    print(f"[seed] {seed.get('title')!r} by {seed.get('artist')!r}  key={seed_key}")

    test_latency(token, seed_key, tracks, reps=8)
    test_exclusion(token, seed_key, tracks)
    test_exclude_scale(token, seed_key, tracks)


if __name__ == "__main__":
    main()
