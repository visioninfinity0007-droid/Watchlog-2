#!/usr/bin/env python3
"""
WatchLog prototype - local PostgREST-shaped stub, backed by SQLite.

WHAT THIS IS FOR
    Exercising the agent's enrollment / heartbeat / dedupe logic over real
    HTTP today, before a Supabase project exists. It implements only the
    handful of PostgREST behaviours the agent actually uses.

WHAT THIS DOES NOT PROVE
    It cannot catch a mismatch between the agent and *real* PostgREST,
    because it was written to match the agent. It proves the agent's own
    state machine, not Supabase's semantics. The live run against a real
    Supabase project is still required, and is the gate that counts.

    python fake_postgrest.py --port 8500 [--db path.sqlite] [--reset]

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

TENANT_ID = "00000000-0000-4000-8000-000000000001"
SITE_ID = "00000000-0000-4000-8000-000000000002"

SCHEMA = """
create table if not exists tenants (
  id text primary key, name text not null, created_at text
);
create table if not exists sites (
  id text primary key, tenant_id text not null, name text not null,
  timezone text, created_at text
);
create table if not exists agents (
  id text primary key, tenant_id text not null, site_id text not null,
  agent_key text not null unique, hostname text, platform text,
  agent_version text, enrolled_at text, last_seen_at text
);
create table if not exists enrollment_codes (
  code text primary key, tenant_id text not null, site_id text not null,
  expires_at text not null, used_at text, used_by_agent_id text,
  created_at text
);
create table if not exists cameras (
  id text primary key, tenant_id text not null, site_id text not null,
  channel text not null, name text, created_at text,
  unique (site_id, channel)
);
create table if not exists events (
  id integer primary key autoincrement,
  tenant_id text not null, site_id text not null, camera_id text,
  agent_id text, event_type text not null, device_event_id text,
  device_ts text not null, agent_ts text not null, received_at text not null,
  dedupe_key text not null, payload text not null default '{}',
  unique (tenant_id, dedupe_key)
);
create view if not exists v_agent_fleet as
select a.id as agent_id, t.name as tenant, s.name as site, a.hostname,
  a.platform, a.agent_version, a.enrolled_at, a.last_seen_at,
  (select max(device_ts) from events e where e.agent_id = a.id) as last_event_at,
  (select count(*)       from events e where e.agent_id = a.id) as event_count,
  (select e.event_type   from events e where e.agent_id = a.id
     order by e.device_ts desc limit 1) as last_event_type,
  (select c.name from events e left join cameras c on c.id = e.camera_id
     where e.agent_id = a.id order by e.device_ts desc limit 1) as last_event_camera
from agents a
join tenants t on t.id = a.tenant_id
join sites   s on s.id = a.site_id;
"""

TABLE_COLUMNS: dict[str, list[str]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def seed(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    for t in ("tenants", "sites", "agents", "enrollment_codes", "cameras",
              "events", "v_agent_fleet"):
        TABLE_COLUMNS[t] = [r[1] for r in db.execute(f"pragma table_info({t})")]

    db.execute("insert or ignore into tenants values (?,?,?)",
               (TENANT_ID, "AKSS (prototype)", now_iso()))
    db.execute("insert or ignore into sites values (?,?,?,?,?)",
               (SITE_ID, TENANT_ID, "Prototype Site A", "Asia/Karachi", now_iso()))
    expires = (datetime.now(timezone.utc) + timedelta(days=7)) \
        .isoformat().replace("+00:00", "Z")
    for code in ("WL-PROTO-DEV-0001", "WL-PROTO-EXE-0001"):
        db.execute(
            "insert or ignore into enrollment_codes "
            "(code, tenant_id, site_id, expires_at, created_at) values (?,?,?,?,?)",
            (code, TENANT_ID, SITE_ID, expires, now_iso()))
    db.commit()


def parse_filters(query: str) -> tuple[str, list]:
    """Translate a PostgREST filter query string into a SQL WHERE clause."""
    clauses: list[str] = []
    params: list = []
    for part in query.split("&"):
        if not part or "=" not in part:
            continue
        col, _, spec = part.partition("=")
        col = unquote(col)
        if col in ("on_conflict", "select", "order", "limit", "offset"):
            continue
        op, _, raw = spec.partition(".")
        val = unquote(raw)
        if op == "eq":
            clauses.append(f'"{col}" = ?')
            params.append(val)
        elif op == "is" and val == "null":
            clauses.append(f'"{col}" is null')
        elif op == "gt":
            clauses.append(f'"{col}" > ?')
            params.append(now_iso() if val == "now" else val)
        elif op == "lt":
            clauses.append(f'"{col}" < ?')
            params.append(now_iso() if val == "now" else val)
        else:
            raise ValueError(f"stub does not implement filter: {part}")
    return (" and ".join(clauses) if clauses else "1=1"), params


def rows_to_dicts(cur: sqlite3.Cursor) -> list[dict]:
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        if "payload" in d and isinstance(d["payload"], str):
            try:
                d["payload"] = json.loads(d["payload"])
            except json.JSONDecodeError:
                pass
        out.append(d)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "FakePostgREST/0.1"
    db: sqlite3.Connection

    # -- plumbing -------------------------------------------------------

    def _cors(self) -> None:
        # Real Supabase sends these; the viewer is a browser client.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "apikey, authorization, content-type, prefer")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def _table(self, path: str) -> str:
        if not path.startswith("/rest/v1/"):
            raise ValueError("unknown path " + path)
        table = path[len("/rest/v1/"):]
        if table not in TABLE_COLUMNS:
            raise ValueError("unknown table " + table)
        return table

    def _guard_key(self) -> bool:
        if not self.headers.get("apikey"):
            self._send({"message": "no api key"}, 401)
            return False
        return True

    # -- verbs ----------------------------------------------------------

    def do_GET(self):  # noqa: N802
        if not self._guard_key():
            return
        url = urlparse(self.path)
        try:
            table = self._table(url.path)
            where, params = parse_filters(url.query)
        except ValueError as e:
            return self._send({"message": str(e)}, 400)
        extras = dict(p.split("=", 1) for p in url.query.split("&")
                      if "=" in p and p.split("=", 1)[0] in ("order", "limit"))
        sql = f'select * from "{table}" where {where}'
        if "order" in extras:
            col, _, direction = unquote(extras["order"]).partition(".")
            if col.replace("_", "").isalnum():
                sql += f' order by "{col}" ' + ("desc" if direction.startswith("desc") else "asc")
        if extras.get("limit", "").isdigit():
            sql += " limit " + extras["limit"]
        cur = self.db.execute(sql, params)
        self._send(rows_to_dicts(cur))

    def do_PATCH(self):  # noqa: N802
        if not self._guard_key():
            return
        url = urlparse(self.path)
        try:
            table = self._table(url.path)
            where, params = parse_filters(url.query)
        except ValueError as e:
            return self._send({"message": str(e)}, 400)

        body = self._body() or {}
        cols = [c for c in body if c in TABLE_COLUMNS[table]]
        if not cols:
            return self._send({"message": "no updatable columns"}, 400)
        sets = ", ".join(f'"{c}" = ?' for c in cols)
        cur = self.db.execute(
            f'update "{table}" set {sets} where {where} returning *',
            [body[c] for c in cols] + params)
        out = rows_to_dicts(cur)
        self.db.commit()
        self._send(out)

    def do_POST(self):  # noqa: N802
        if not self._guard_key():
            return
        url = urlparse(self.path)
        try:
            table = self._table(url.path)
        except ValueError as e:
            return self._send({"message": str(e)}, 400)

        prefer = self.headers.get("Prefer") or ""
        rows = self._body()
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return self._send([])

        if "ignore-duplicates" in prefer:
            conflict = "or ignore"
        elif "merge-duplicates" in prefer:
            conflict = "merge"
        else:
            conflict = ""

        out: list[dict] = []
        for row in rows:
            row = dict(row)
            if table in ("agents", "cameras") and "id" not in row:
                row["id"] = str(uuid.uuid4())
            if table == "events":
                row.setdefault("received_at", now_iso())
            if table == "agents":
                row.setdefault("enrolled_at", now_iso())
            if "payload" in row and not isinstance(row["payload"], str):
                row["payload"] = json.dumps(row["payload"])

            cols = [c for c in row if c in TABLE_COLUMNS[table]]
            names = ", ".join(f'"{c}"' for c in cols)
            marks = ", ".join("?" for _ in cols)
            vals = [row[c] for c in cols]

            if conflict == "merge":
                target = "site_id, channel" if table == "cameras" else "id"
                upd = ", ".join(f'"{c}" = excluded."{c}"'
                                for c in cols if c not in ("id",))
                sql = (f'insert into "{table}" ({names}) values ({marks}) '
                       f'on conflict ({target}) do update set {upd} returning *')
            elif conflict == "or ignore":
                sql = (f'insert or ignore into "{table}" ({names}) '
                       f'values ({marks}) returning *')
            else:
                sql = f'insert into "{table}" ({names}) values ({marks}) returning *'

            try:
                cur = self.db.execute(sql, vals)
                out.extend(rows_to_dicts(cur))
            except sqlite3.IntegrityError as e:
                self.db.rollback()
                return self._send({"message": str(e)}, 409)

        self.db.commit()
        self._send(out, 201)

    def log_message(self, fmt, *args):
        print(f"[fake-postgrest] {fmt % args}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="PostgREST-shaped stub for local gates")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8500)
    ap.add_argument("--db", default="tests/prototype.sqlite")
    ap.add_argument("--reset", action="store_true", help="drop and reseed the db file")
    args = ap.parse_args()

    if args.reset:
        import os
        if os.path.exists(args.db):
            os.remove(args.db)
            print(f"[fake-postgrest] removed {args.db}", flush=True)

    conn = sqlite3.connect(args.db, check_same_thread=False, isolation_level=None)
    conn.execute("pragma journal_mode=WAL")
    conn.isolation_level = ""
    seed(conn)
    Handler.db = conn

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[fake-postgrest] http://{args.host}:{args.port}  db={args.db}", flush=True)
    print("[fake-postgrest] NOT Supabase. Proves the agent, not PostgREST.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[fake-postgrest] stopped", flush=True)


if __name__ == "__main__":
    main()
