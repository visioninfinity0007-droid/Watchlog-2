from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def main():
    problems = []
    migration = read("prototype/supabase/migrations/0116_camera_preview_realtime.sql")
    portal = read("portal/app/control-room/customer-workspace.js")

    for token in [
        "camera_snapshot_signals",
        "wl_signal_camera_preview",
        "trg_camera_config_snapshot_realtime_signal",
        "trg_event_snapshot_realtime_signal",
        "after insert or update on public.camera_config_snapshots",
        "after insert on public.snapshots",
        "public.wl_is_member(tenant_id)",
        "grant select on public.camera_snapshot_signals to authenticated",
        "alter publication supabase_realtime add table public.camera_snapshot_signals",
        "create or replace function public.wl_camera_config_snapshot",
        "from public.camera_config_snapshots cs",
        "from public.snapshots s",
    ]:
        if token not in migration:
            problems.append(f"camera preview realtime migration missing: {token}")

    for raw_table in ("camera_config_snapshots", "snapshots"):
        statement = (
            "alter publication supabase_realtime add table public." + raw_table
        )
        if statement in migration:
            problems.append(
                f"raw {raw_table} image bytes must never be published to Realtime"
            )

    for token in [
        'wl_camera_config_snapshot',
        '.from("camera_snapshot_signals")',
        '"postgres_changes"',
        '"visibilitychange"',
        '"focus"',
        "removeChannel",
        "site_id=eq.",
        "reconcileSignals",
    ]:
        if token not in portal:
            problems.append(f"customer camera portal missing realtime behavior: {token}")

    if '.from("camera_config_snapshots")' in portal or '.from("snapshots")' in portal:
        problems.append(
            "browser must fetch image bytes through wl_camera_config_snapshot, not raw image tables"
        )

    if problems:
        raise SystemExit(
            "camera preview realtime contract failed:\n- " + "\n- ".join(problems)
        )
    print("camera preview realtime contract: PASS")


if __name__ == "__main__":
    main()
