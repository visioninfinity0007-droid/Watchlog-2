#!/usr/bin/env python3
"""Product contract checks for site lifecycle, notifications and customer-safe AI."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MIG=ROOT/"prototype"/"supabase"/"migrations"/"0117_site_lifecycle_notifications_camera_identity.sql"
SETTINGS=ROOT/"portal"/"app"/"settings"/"site-list.js"
NOTIFS=ROOT/"portal"/"app"/"notifications"/"customer-workspace.js"
NAV=ROOT/"portal"/"app"/"nav-config.js"
AI=ROOT/"prototype"/"supabase"/"functions"/"watchlog-ai"/"index.ts"
CHAT=ROOT/"prototype"/"supabase"/"migrations"/"0101_ai_workspace.sql"

def main():
    problems=[]
    sql=MIG.read_text(encoding="utf-8")
    for token in (
        "is_canonical boolean not null default true",
        "wl_remove_site",
        "delete from ai_conversations",
        "delete from sites",
        "notification_reads",
        "wl_notifications(",
        "wl_notification_mark_read",
        "wl_notifications_mark_all_read",
        "coalesce(c.is_canonical,true)",
    ):
        if token not in sql: problems.append(f"migration missing {token}")

    settings=SETTINGS.read_text(encoding="utf-8")
    for token in ("Remove site and disconnect Agent","p_confirm_name","settings.removeSite"):
        if token not in settings: problems.append(f"site-removal UI missing {token}")

    notifs=NOTIFS.read_text(encoding="utf-8")
    for token in ("wl_notifications","wl_notification_mark_read","wl_notifications_mark_all_read","What needs your attention"):
        if token not in notifs: problems.append(f"notification UI missing {token}")

    nav=NAV.read_text(encoding="utf-8")
    if '["Notifications","/notifications/","Notifications"]' not in nav:
        problems.append("Notifications missing from primary navigation")

    ai=AI.read_text(encoding="utf-8")
    if "security and office intelligence assistant" in ai:
        problems.append("AI still hard-codes office-only identity")
    for token in ("security and business-operations intelligence assistant","SITE OPERATING CONTEXT","When verified WatchLog evidence supports a direct answer"):
        if token not in ai: problems.append(f"AI site-type prompt missing {token}")

    chat=CHAT.read_text(encoding="utf-8")
    for token in ("c.tenant_id = v_tenant and c.user_id = auth.uid()","m.tenant_id=v_tenant and m.user_id=auth.uid()"):
        if token not in chat: problems.append("chat history is not tenant+user scoped")

    if problems:
        raise SystemExit("site lifecycle/notification contract FAILED:\n- "+"\n- ".join(problems))
    print("site lifecycle/notification contract: PASS")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
