#!/usr/bin/env python3
"""Contract for the two migrations that were applied to production before they
were committed to git (MNVR WP-0 / PR-0 ledger alignment).

0144_portal_qa_truth_contracts and 0145_camera_preview_performance were applied
through the Supabase migration API on 2026-10-02 and exist only in production's
supabase_migrations.schema_migrations ledger. Their exact text was read back from
that ledger (statements[1]) and committed byte for byte. This test pins:

  * file identity: the LF-normalized sha256 we computed, plus the length and md5
    production recorded for the statement text (md5(statements[1]));
  * function identity: md5 of each function body equals md5(pg_proc.prosrc) read
    from production on 2026-10-05, so the files reproduce the live functions;
  * grants: SECURITY DEFINER functions pin search_path, the functions these files
    introduce are revoked from public/anon and granted to authenticated and
    service_role only, and nothing is granted to anon or public.

    python -m pytest prototype/tests/test_production_only_migrations.py -q   # static
    python prototype/tests/test_production_only_migrations.py --pg           # disposable Postgres

The --pg mode checks the applied chain on the DISPOSABLE CI Postgres only (it
refuses to run unless WATCHLOG_CI_PLAIN_POSTGRES=1) and never touches production.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

MIG = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

# name -> (LF-normalized sha256 computed locally, production md5(statements[1]),
#          production length(statements[1]), production supabase_migrations version)
PRODUCTION_ONLY = {
    "0144_portal_qa_truth_contracts.sql": (
        "97afe9f5863f094134c30644a2972a4f1aa388f640c7ed97770535bef99a801d",
        "e44730f590e1ecfd4fa82053b4867e7d",
        24004,
        "20261002224653",
    ),
    "0145_camera_preview_performance.sql": (
        "2b65b7c1c0022f12cd255acaaa149d16afbf2576248bc435230bda6d153e573a",
        "2a2d4409ee216c8b741c7bdd9134253b",
        3144,
        "20261002224736",
    ),
}

# signature -> (file, md5(prosrc) in production, security definer, proconfig,
#               anon execute, authenticated execute, PUBLIC execute) as read 2026-10-05.
PRODUCTION_FUNCTIONS = {
    "wl_analytics_valid_purpose(text)": (
        "0144_portal_qa_truth_contracts.sql", "4832afe1918810a8cde78aa6e3d504a4",
        False, "search_path=public, pg_temp", True, True, True),
    "wl_analytics_catalog()": (
        "0144_portal_qa_truth_contracts.sql", "897286f1bd7b09fdcd592b4ecb7a269d",
        False, "search_path=public", False, True, False),
    "wl_notifications(uuid,integer)": (
        "0144_portal_qa_truth_contracts.sql", "322f0133fe7da86741196018029a48aa",
        True, "search_path=public, pg_temp", False, True, False),
    "wl_my_latest_report_snapshot(uuid)": (
        "0144_portal_qa_truth_contracts.sql", "ec6d5c9c57607fc9bbc02b96a0883741",
        True, "search_path=public, pg_temp", False, True, False),
    "wl_owner_site_truth(uuid)": (
        "0144_portal_qa_truth_contracts.sql", "f9532f07a3a1f5bdabf19d8b0587f101",
        True, "search_path=public, pg_temp", False, True, False),
    "wl_restaurant_day_truth(uuid,date)": (
        "0144_portal_qa_truth_contracts.sql", "dece7cf3f15fc6d0484e24dddff91530",
        True, "search_path=public, pg_temp", False, True, False),
    "wl_portal_overview(integer)": (
        "0144_portal_qa_truth_contracts.sql", "be09c52cab1ff2f987ad1c1d640a3347",
        True, "search_path=public", False, True, False),
    "wl_camera_config_snapshot(uuid)": (
        "0145_camera_preview_performance.sql", "dc9ae9ea90bb6e9730b8f74706ac07fd",
        True, "search_path=public", False, True, False),
    "wl_camera_config_snapshots(uuid[])": (
        "0145_camera_preview_performance.sql", "a9a95d896e69bd105b97baa68631ab4e",
        True, "search_path=public, pg_temp", False, True, False),
}

# Functions first introduced by these files: explicit revoke + grant required.
INTRODUCED = {
    "wl_my_latest_report_snapshot(uuid)",
    "wl_owner_site_truth(uuid)",
    "wl_restaurant_day_truth(uuid,date)",
    "wl_camera_config_snapshots(uuid[])",
}

FN = re.compile(
    r"create or replace function public\.(\w+)\((.*?)\)\s*\nreturns (.*?)\nas \$function\$(.*?)\$function\$;",
    re.S,
)


def _normalized(name: str) -> bytes:
    raw = (MIG / name).read_bytes()
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _text(name: str) -> str:
    return _normalized(name).decode("utf-8")


def _signature(fname: str, args: str) -> str:
    """public.f(\n  p_a uuid default null::uuid,\n  p_b integer default 50\n) -> f(uuid,integer)."""
    types = []
    for arg in (a.strip() for a in args.split(",") if a.strip()):
        parts = re.split(r"\s+default\s+", arg, flags=re.I)[0].split()
        types.append(parts[-1])
    return f"{fname}({','.join(types)})"


def _functions() -> dict[str, tuple[str, str, str]]:
    found = {}
    for name in PRODUCTION_ONLY:
        for m in FN.finditer(_text(name)):
            found[_signature(m.group(1), m.group(2))] = (name, m.group(3), m.group(4))
    return found


def test_files_match_the_production_ledger_identity():
    for name, (sha256, md5, length, _version) in PRODUCTION_ONLY.items():
        data = _normalized(name)
        assert len(data) == length, (name, len(data))
        assert hashlib.md5(data).hexdigest() == md5, name
        assert hashlib.sha256(data).hexdigest() == sha256, name
        assert data.decode("ascii")  # production text is pure ASCII, no BOM


def test_file_bodies_equal_the_live_production_functions():
    found = _functions()
    assert set(found) == set(PRODUCTION_FUNCTIONS), sorted(set(found) ^ set(PRODUCTION_FUNCTIONS))
    for sig, (name, prosrc_md5, *_rest) in PRODUCTION_FUNCTIONS.items():
        fname, _header, body = found[sig]
        assert fname == name, sig
        assert hashlib.md5(body.encode("utf-8")).hexdigest() == prosrc_md5, sig


def test_security_definer_functions_pin_search_path():
    for sig, (_name, header, _body) in _functions().items():
        secdef = PRODUCTION_FUNCTIONS[sig][2]
        assert ("security definer" in header) == secdef, sig
        expected = PRODUCTION_FUNCTIONS[sig][3].split("=", 1)[1].replace(" ", "")
        pinned = re.search(r"\nset search_path to (.+)", header)
        assert pinned, sig
        assert pinned.group(1).replace("'", "").replace(" ", "") == expected, sig


def test_introduced_functions_are_authenticated_only():
    for sig in INTRODUCED:
        name = PRODUCTION_FUNCTIONS[sig][0]
        sql = _text(name)
        assert f"revoke all on function public.{sig} from public, anon;" in sql, sig
        assert f"grant execute on function public.{sig} to authenticated, service_role;" in sql, sig


def test_no_grant_to_anon_or_public():
    for name in PRODUCTION_ONLY:
        for line in _text(name).splitlines():
            low = line.strip().lower()
            if low.startswith("grant "):
                grantees = low.split(" to ", 1)[1]
                assert "anon" not in grantees and "public" not in grantees, (name, line)


def _pg_check() -> None:
    if os.environ.get("WATCHLOG_CI_PLAIN_POSTGRES") != "1" or not os.environ.get("SUPABASE_DB_HOST"):
        sys.exit("FATAL: --pg runs only against the disposable CI Postgres "
                 "(WATCHLOG_CI_PLAIN_POSTGRES=1 and SUPABASE_DB_* set).")
    import psycopg

    conn = psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), autocommit=True)
    with conn:
        applied = {r[0] for r in conn.execute("select filename from schema_migrations")}
        missing = set(PRODUCTION_ONLY) - applied
        assert not missing, f"not applied: {sorted(missing)}"
        rows = conn.execute(
            """
            select p.oid::regprocedure::text, md5(p.prosrc), p.prosecdef,
                   array_to_string(p.proconfig, ';'),
                   has_function_privilege('anon', p.oid, 'execute'),
                   has_function_privilege('authenticated', p.oid, 'execute'),
                   p.proacl is null or p.proacl::text ~ '(^\\{|,)=X',
                   has_function_privilege('service_role', p.oid, 'execute')
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname = any(%s)
            """,
            ([s.split("(", 1)[0] for s in PRODUCTION_FUNCTIONS],),
        ).fetchall()
    live = {r[0]: r[1:] for r in rows}
    assert set(live) == set(PRODUCTION_FUNCTIONS), sorted(set(live) ^ set(PRODUCTION_FUNCTIONS))
    for sig, (_name, prosrc_md5, secdef, cfg, anon_x, auth_x, public_x) in PRODUCTION_FUNCTIONS.items():
        got = live[sig]
        assert got[:6] == (prosrc_md5, secdef, cfg, anon_x, auth_x, public_x), (sig, got)
        if sig in INTRODUCED:
            assert got[6] is True, (sig, "service_role must keep execute")
    print(f"OK: {len(live)} production-only functions match production bodies and grants")


if __name__ == "__main__":
    if "--pg" in sys.argv[1:]:
        _pg_check()
        sys.exit(0)
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} production-only migration contracts passed")
