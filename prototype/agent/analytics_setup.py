"""Analytics-aware extension to the proven WatchLog recorder setup.

The base wizard proves recorder discovery, authentication and camera discovery.
This optional final context step classifies the site and camera purposes so the
portal can start with useful Analytics Studio recommendations. Lines, zones and
schedules remain in the portal where they can be drawn against a camera still.
"""
from __future__ import annotations

import json

import setup_wizard as base
from drivers import autodetect

BASE_RUN = base.run

SITE_TYPES = [
    ("retail", "Retail store"),
    ("warehouse_logistics", "Warehouse / logistics"),
    ("manufacturing", "Factory / manufacturing"),
    ("office_commercial", "Office / commercial building"),
    ("school_campus", "School / campus"),
    ("parking_yard", "Parking / yard"),
    ("residential_community", "Residential / gated community"),
    ("custom", "Other / custom"),
]
PURPOSES = [
    ("entrance_exit", "Entrance / Exit"), ("main_gate", "Main Gate"),
    ("reception", "Reception"), ("checkout_till", "Checkout / Till"),
    ("loading_bay", "Loading Bay"), ("warehouse_floor", "Warehouse Floor"),
    ("perimeter", "Perimeter"), ("restricted_area", "Restricted Area"),
    ("parking", "Parking"), ("office_floor", "Office Floor"),
    ("school_gate", "School Gate"), ("corridor", "Corridor"),
    ("custom", "Other / configure later"),
]
DEFAULTS_BY_SITE = {
    "retail": "entrance_exit", "warehouse_logistics": "warehouse_floor",
    "manufacturing": "warehouse_floor", "office_commercial": "office_floor",
    "school_campus": "school_gate", "parking_yard": "parking",
    "residential_community": "main_gate", "custom": "custom",
}


def _choose(title, items, default_index=None):
    print(f"\n  {title}\n")
    for index, (_key, label) in enumerate(items, 1):
        print(f"    {index:>2}. {label}")
    if default_index is None:
        default_index = len(items)
    while True:
        raw = base.ask("Choose", str(default_index))
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1][0]
        print("  Please choose one of the numbers above.")


def _purpose_default(channel_name, site_type):
    name = (channel_name or "").lower()
    heuristics = [
        (("load", "dock", "bay"), "loading_bay"),
        (("till", "checkout", "cash", "counter"), "checkout_till"),
        (("reception", "lobby"), "reception"),
        (("parking", "car park"), "parking"),
        (("perimeter", "boundary", "fence"), "perimeter"),
        (("corridor", "hall"), "corridor"), (("gate",), "main_gate"),
        (("entry", "entrance", "door"), "entrance_exit"),
    ]
    for words, purpose in heuristics:
        if any(word in name for word in words):
            return purpose
    return DEFAULTS_BY_SITE.get(site_type, "custom")


def _purpose_index(key):
    for index, (candidate, _label) in enumerate(PURPOSES, 1):
        if candidate == key:
            return index
    return len(PURPOSES)


def _purpose_label(key):
    return next((label for candidate, label in PURPOSES if candidate == key), key)


def _classify_cameras(values, site_type):
    print("\n  WatchLog can suggest camera purposes from the names on the recorder.")
    print("  These can be changed later in Analytics Studio.\n")
    driver = None
    try:
        driver, _info = autodetect(values["nvr_url"], values["nvr_username"],
                                   values["nvr_password"], timeout=8,
                                   log=lambda _message: None)
        channels = driver.list_channels()
    except Exception as error:
        print(f"  Camera names could not be re-read ({type(error).__name__}).")
        print("  Recorder setup is still complete. Assign camera purposes later in the portal.")
        return []
    finally:
        if driver:
            try:
                driver.close()
            except Exception:
                pass

    suggestions = [{
        "channel": str(camera.channel),
        "name": camera.name or f"Camera {camera.channel}",
        "purpose": _purpose_default(camera.name, site_type),
        "analytics_enabled": True,
    } for camera in channels]
    if not suggestions:
        return []

    print("  Suggested camera purposes:")
    for profile in suggestions:
        print(f"    ch{profile['channel']:<4} {profile['name']:<24} "
              f"-> {_purpose_label(profile['purpose'])}")

    if base.ask_yes("\n  Use these suggestions", True):
        if not base.ask_yes("Review any camera individually now", False):
            return suggestions

    profiles = []
    for index, profile in enumerate(suggestions, 1):
        print(f"\n  Camera {index}/{len(suggestions)}: channel "
              f"{profile['channel']}  {profile['name']}")
        suggested = profile["purpose"]
        if base.ask_yes(f"Use suggested purpose '{_purpose_label(suggested)}'", True):
            purpose = suggested
        else:
            purpose = _choose("What does this camera watch?", PURPOSES,
                              _purpose_index(suggested))
        profiles.append({**profile, "purpose": purpose})
    return profiles


def run(cfg_path, supabase_url, publishable_key, enrollment_code):
    values = BASE_RUN(cfg_path, supabase_url, publishable_key, enrollment_code)
    if not values:
        return values

    print("\n  Monitoring context (optional, recommended)")
    print("  " + "-" * 51)
    print("  The recorder is already proven. This final context helps WatchLog")
    print("  recommend useful monitoring goals for each camera in the portal.")

    site_type = _choose("What kind of site is this?", SITE_TYPES, len(SITE_TYPES))
    profiles = _classify_cameras(values, site_type)
    values["site_type"] = site_type
    values["camera_profiles_json"] = json.dumps(profiles, separators=(",", ":"))

    print("\n  WatchLog setup is complete.")
    print("  The Site Agent will run in the background and sync these camera purposes.")
    print("  In the portal, open Analytics Studio only if you want to add monitoring")
    print("  goals such as visitor flow, vehicle flow, zones or after-hours schedules.")
    return values
