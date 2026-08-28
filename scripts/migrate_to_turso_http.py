#!/usr/bin/env python3
"""
Migrate local SQLite → Turso using Turso's HTTP Hrana v2 pipeline API
directly. No libsql-client dependency — uses `requests` only.

Runs statements in batches (default 50 per HTTP POST) with detailed
per-batch error reporting so we can see exactly which row breaks if any.

Usage:
    python scripts/migrate_to_turso_http.py \
        --source data/vibescape.db \
        --target-url  https://vibescape-<user>.<region>.turso.io \
        --target-token <jwt>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema.sql"
TABLES_IN_ORDER = ["users", "sessions", "tracks", "user_tracks"]
BATCH_SIZE = 50


def _pipeline_url(base: str) -> str:
    return base.rstrip("/") + "/v2/pipeline"


def _hrana_arg(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": float(v)}
    if isinstance(v, (bytes, bytearray)):
        import base64
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _execute_stmt(sql: str, params=None) -> dict:
    stmt = {"sql": sql}
    if params:
        stmt["args"] = [_hrana_arg(p) for p in params]
    return {"type": "execute", "stmt": stmt}


def _post_pipeline(session: requests.Session, url: str, token: str, requests_list: list) -> dict:
    body = {"baton": None, "requests": requests_list}
    resp = session.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _check_pipeline_results(payload: dict, batch_desc: str) -> None:
    results = payload.get("results", [])
    for i, r in enumerate(results):
        if r.get("type") == "error":
            err = r.get("error", {})
            raise RuntimeError(
                f"[batch {batch_desc}] statement {i} failed: "
                f"code={err.get('code')} message={err.get('message')}"
            )


def _apply_schema(session, url, token) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    stmts = [s.strip() for s in schema_sql.split(";") if s.strip()]
    print(f"[schema] applying {len(stmts)} statements")
    reqs = [_execute_stmt(s) for s in stmts] + [{"type": "close"}]
    payload = _post_pipeline(session, url, token, reqs)
    _check_pipeline_results(payload, "schema")
    print("[schema] applied")


def _count_target(session, url, token, table: str) -> int:
    reqs = [_execute_stmt(f"SELECT COUNT(*) FROM {table}"), {"type": "close"}]
    payload = _post_pipeline(session, url, token, reqs)
    _check_pipeline_results(payload, f"count:{table}")
    rows = payload["results"][0]["response"]["result"]["rows"]
    if not rows:
        return 0
    v = rows[0][0]
    if isinstance(v, dict):
        return int(v.get("value", 0))
    return int(v)


def _target_cols(session, url, token, table: str) -> list[str]:
    reqs = [_execute_stmt(f"PRAGMA table_info({table})"), {"type": "close"}]
    payload = _post_pipeline(session, url, token, reqs)
    _check_pipeline_results(payload, f"target_cols:{table}")
    rows = payload["results"][0]["response"]["result"]["rows"]
    def _v(cell):
        return cell.get("value") if isinstance(cell, dict) else cell
    return [_v(r[1]) for r in rows]


def _copy_table(session, url, token, src: sqlite3.Connection, table: str) -> int:
    src_cursor = src.execute(f"SELECT * FROM {table} LIMIT 0")
    src_cols = [d[0] for d in src_cursor.description]
    tgt_cols = _target_cols(session, url, token, table)
    keep = [c for c in src_cols if c in tgt_cols]
    skipped = [c for c in src_cols if c not in tgt_cols]
    if skipped:
        print(f"[{table}] source has {len(skipped)} legacy col(s) not in target — skipping: {skipped}")
    select_cols = ", ".join(keep)
    placeholders = ", ".join(["?"] * len(keep))
    col_list = ", ".join(keep)
    cursor = src.execute(f"SELECT {select_cols} FROM {table}")
    insert_sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"

    total = 0
    batch = []
    row_num = 0
    while True:
        chunk = cursor.fetchmany(BATCH_SIZE)
        if not chunk:
            break
        for row in chunk:
            batch.append(_execute_stmt(insert_sql, list(row)))
            row_num += 1
            if len(batch) >= BATCH_SIZE:
                reqs = batch + [{"type": "close"}]
                payload = _post_pipeline(session, url, token, reqs)
                try:
                    _check_pipeline_results(payload, f"{table}[{row_num - len(batch) + 1}..{row_num}]")
                except RuntimeError as e:
                    print(f"[{table}] {e}", file=sys.stderr)
                    raise
                total += len(batch)
                batch = []
                if total % 200 == 0:
                    print(f"[{table}] {total} rows copied so far")

    if batch:
        reqs = batch + [{"type": "close"}]
        payload = _post_pipeline(session, url, token, reqs)
        _check_pipeline_results(payload, f"{table}[final {len(batch)} rows]")
        total += len(batch)

    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target-url", required=True, help="https://<db>.turso.io")
    ap.add_argument("--target-token", required=True)
    args = ap.parse_args()

    src_path = Path(args.source).resolve()
    if not src_path.exists():
        print(f"[migrate] source not found: {src_path}", file=sys.stderr)
        return 2

    url = _pipeline_url(args.target_url)
    token = args.target_token

    session = requests.Session()

    print(f"[migrate] source: {src_path}")
    print(f"[migrate] target: {args.target_url}")

    _apply_schema(session, url, token)

    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row

    grand_total = 0
    try:
        for table in TABLES_IN_ORDER:
            src_count = int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            before = _count_target(session, url, token, table)
            print(f"[{table}] source={src_count} target(before)={before} ...")
            copied = _copy_table(session, url, token, src, table)
            after = _count_target(session, url, token, table)
            print(f"[{table}] copied={copied} target(after)={after}")
            grand_total += copied
    finally:
        src.close()

    print(f"[migrate] done. total rows copied: {grand_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
