"""WatchLog Site Agent first-run recorder setup.

The wizard discovers the recorder, proves its local credentials before saving
anything, discovers cameras, then links the installation with a one-time WatchLog
enrollment code. Recorder credentials remain in the local INI file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import discover
import wsdiscovery
from drivers import DriverError, autodetect

BANNER = r"""
  =========================================================
   WatchLog Site Agent
   Add intelligence to the CCTV recorder already at site
  =========================================================

  Recorder credentials stay on this PC. WatchLog connects
  outward only and does not expose the recorder to the web.
"""


def step(number: int, title: str, detail: str = "") -> None:
    print(f"\n  [{number}/4] {title}")
    print("  " + "-" * 51)
    if detail:
        print(f"  {detail}\n")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        got = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled. No new configuration was saved.")
        raise SystemExit(1) from None
    return got or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    got = ask(f"{prompt} ({d})").lower()
    if not got:
        return default
    return got.startswith("y")


def pause() -> None:
    try:
        input("\n  Press Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass


def choose_recorder() -> str | None:
    """Find recorders on the LAN and let the installer choose one."""
    step(1, "Find the recorder", "WatchLog first asks ONVIF devices, then searches the local network if needed.")
    print("  Asking the network for ONVIF devices (about 4 seconds)...\n")
    announced = wsdiscovery.discover(log=print)
    wsdiscovery.report(announced, log=print)

    if announced:
        print("\n  Recorder candidates:\n")
        for i, found in enumerate(announced, 1):
            label = " ".join(x for x in (found.name, found.hardware) if x) or "unnamed device"
            print(f"    {i}. {found.ip:<16} {label}")
        print(f"    {len(announced) + 1}. None of these - search the network more deeply\n")
        pick = ask("Choose a recorder", "1")
        if pick.isdigit() and 1 <= int(pick) <= len(announced):
            return announced[int(pick) - 1].ip

    print("\n  Searching this local network. This can take about 20 seconds.\n")
    hits = discover.sweep(None, log=lambda m: print("  " + m.strip()))
    candidates: list[tuple[str, list[int]]] = []
    for ip, ports in hits:
        ports = sorted(ports)
        if any(p in ports for p in (37777, 34567, 554, 8000, 8080)):
            candidates.append((ip, ports))

    if not candidates:
        print("\n  WatchLog did not find a recorder automatically.")
        print("  Check that this PC is on the same network, the recorder is switched on,")
        print("  and its network/web service is enabled.")
        if ask_yes("Do you know the recorder IP address and want to enter it"):
            return ask("Recorder IP, for example 192.168.1.108") or None
        return None

    print("\n  Recorder candidates:\n")
    for i, (ip, ports) in enumerate(candidates, 1):
        hint = ""
        if 37777 in ports: hint = "  (Dahua family)"
        elif 34567 in ports: hint = "  (unbranded/Xiongmai - not supported)"
        elif 554 in ports: hint = "  (video stream detected)"
        print(f"    {i}. {ip}    ports {', '.join(str(p) for p in ports)}{hint}")
    print(f"    {len(candidates) + 1}. None of these - enter an address myself\n")
    while True:
        pick = ask("Choose a recorder", "1")
        if pick.isdigit():
            n = int(pick)
            if 1 <= n <= len(candidates): return candidates[n - 1][0]
            if n == len(candidates) + 1: return ask("Recorder IP") or None
        print("  Please enter one of the numbers above.")


def try_connect(ip: str, user: str, password: str):
    """Try sensible local web ports and prove which recorder driver works."""
    ports = [80, 8000, 8080, 81, 88, 8081]
    try:
        found = [r.port for r in discover.scan(ip, log=lambda m: None)
                 if r.open and r.kind in ("http", "https")]
        ports = found + [p for p in ports if p not in found]
    except Exception:  # noqa: BLE001
        pass
    tried = []
    for port in ports:
        url = f"http://{ip}" if port == 80 else f"http://{ip}:{port}"
        print(f"  Testing {url} ...", end=" ", flush=True)
        try:
            driver, info = autodetect(url, user, password, timeout=6, log=lambda m: None)
            print("connected")
            return driver, info, url
        except DriverError as exc:
            reason = "no answer"
            for line in str(exc).splitlines():
                low = line.lower()
                if "401" in low or "unauthor" in low: reason = "wrong username or password"; break
                if "refused" in low: reason = "nothing listening"; break
                if "timed out" in low: reason = "no answer"; break
            print(reason); tried.append((url, reason))
            if reason == "wrong username or password":
                return None, None, ("auth", url)
    return None, None, ("none", tried)


def run(cfg_path: Path, supabase_url: str, publishable_key: str,
        enrollment_code: str) -> dict | None:
    """Walk the proven four-step setup. Nothing is persisted until all checks pass."""
    print(BANNER)
    me = discover.local_ipv4()
    print(f"\n  This site PC is {me}." if me else "\n  WatchLog could not read this PC's network address.")

    ip = choose_recorder()
    if not ip:
        print("\n  Setup stopped because no recorder was selected.")
        return None

    step(2, "Verify the recorder login", "Use the recorder's own account, not a mobile-app or WatchLog password.")
    print(f"  Selected recorder: {ip}")
    driver = info = url = None
    for attempt in range(3):
        user = ask("Recorder username", "admin")
        password = ask("Recorder password")
        if not password:
            print("  A recorder password is required.\n"); continue
        print(); driver, info, url = try_connect(ip, user, password)
        if driver: break
        if isinstance(url, tuple) and url[0] == "auth":
            print(f"\n  The recorder at {url[1]} rejected that login.")
            if attempt < 2: print("  Check the recorder account and try again.\n")
            continue
        print("\n  WatchLog could not reach the recorder web service at that address.")
        print("  On the recorder, check Network > Port and confirm HTTP/HTTPS is enabled.")
        if ask_yes("Try a different recorder address"):
            ip = ask("Recorder IP") or ip; continue
        return None
    if not driver:
        print("\n  Setup stopped. The recorder connection was not proven.")
        return None

    print("\n  Recorder connection proven:")
    print(f"    Make       {info.vendor}")
    print(f"    Model      {info.model or 'unknown'}")
    print(f"    Firmware   {info.firmware or 'unknown'}")
    print(f"    Driver     {driver.name}")

    step(3, "Discover the cameras", "WatchLog reads camera channels from the recorder; it does not open inbound access.")
    try:
        channels = driver.list_channels()
        print(f"  Found {len(channels)} camera(s):")
        for camera in channels:
            print(f"    Channel {camera.channel:>3}  {camera.name or 'unnamed camera'}")
    except DriverError as exc:
        print(f"  Recorder connected, but camera names could not be listed: {str(exc).splitlines()[0][:120]}")
        channels = []
    if not driver.verified_against_hardware:
        print("\n  Compatibility note: this exact recorder model is not yet field-validated.")
        print("  The login and protocol worked; review its first day of events after setup.")
    driver.close()

    step(4, "Link this site to WatchLog", "The one-time enrollment code identifies the site. Recorder credentials are not uploaded.")
    code = enrollment_code
    if code:
        print("  An enrollment code is already included with this installer.")
    else:
        code = ask("WatchLog enrollment code")
        if not code:
            print("  An enrollment code is required to link this installation.")
            return None

    print("\n  Setup checks are complete.")
    print("  Recorder connection: proven")
    print(f"  Cameras discovered:   {len(channels)}")
    print("  Network model:        outbound only")
    if not ask_yes("Save this proven configuration and start WatchLog"):
        return None

    return {
        "supabase_url": supabase_url,
        "supabase_publishable_key": publishable_key,
        "enrollment_code": code,
        "nvr_url": url,
        "nvr_username": user,
        "nvr_password": password,
        "nvr_driver": "auto",
    }


def write_config(path: Path, values: dict) -> None:
    """Write UTF-8 without BOM; the file contains the local recorder password."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["; WatchLog - written after recorder setup was proven.",
             "; Contains this site's recorder password. Keep it on this PC.",
             "", "[watchlog]"]
    for key, value in values.items():
        lines.append(f"{key} = {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  WatchLog configuration saved to {path}")
