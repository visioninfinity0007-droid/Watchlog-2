"""
First-run setup wizard.

The problem this solves: every failure so far has been configuration, not
code. Someone has to know the recorder's IP address, know it is not the
one in the phone app, know which port the web interface moved to, and
then hand-edit an INI file without letting Notepad add a BOM. That is not
something you can ask an installer - or a client - to get right.

The wizard finds the recorder itself, asks for the password, PROVES the
connection works before saving anything, and writes the config file.

Design rules, learned the hard way:
  - Never save a config that has not been tested. A file that looks
    correct and silently does not work is worse than no file.
  - Never dead-end. Every failure offers the next thing to try.
  - Assume the person running this has never seen a DVR menu.
"""

from __future__ import annotations

import sys
from pathlib import Path

import discover
import wsdiscovery
from drivers import DriverError, autodetect, build

BANNER = r"""
  ---------------------------------------------
     WatchLog  -  camera recorder setup
  ---------------------------------------------
"""


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        got = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  cancelled.")
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


# ---------------------------------------------------------------------

def choose_recorder() -> str | None:
    """
    Find recorders on the LAN and let the user pick one.

    Two stages, cheapest and most reliable first:

      1. ONVIF WS-Discovery. One multicast probe; every ONVIF device
         answers with its own address and the port its service runs on.
         This is what SADP and ConfigTool do, and what every cloud VMS
         uses to onboard an existing fleet. ~4 seconds, nothing inferred.

      2. A port sweep of the whole /24. Slower and it can only guess a
         recorder from an open port number - but it still finds devices
         that have ONVIF discovery switched off, which many do.
    """
    print("  Asking the network for ONVIF devices (about 4 seconds)...\n")
    announced = wsdiscovery.discover(log=print)
    wsdiscovery.report(announced, log=print)

    if announced:
        print("\n  Found:\n")
        for i, f in enumerate(announced, 1):
            label = " ".join(x for x in (f.name, f.hardware) if x) or "unnamed"
            print(f"    {i}. {f.ip:<16} {label}")
        print(f"    {len(announced) + 1}. none of these - search harder\n")
        pick = ask("Which one", "1")
        if pick.isdigit() and 1 <= int(pick) <= len(announced):
            return announced[int(pick) - 1].ip

    print("\n  Scanning every address on this network. About 20 seconds.\n")
    hits = discover.sweep(None, log=lambda m: print("  " + m.strip()))

    candidates: list[tuple[str, list[int]]] = []
    for ip, ports in hits:
        ports = sorted(ports)
        # A recorder gives itself away with a video or SDK port. A device
        # with only :80 is far more likely to be the router.
        if any(p in ports for p in (37777, 34567, 554)) or \
                any(p in ports for p in (8000, 8080)):
            candidates.append((ip, ports))

    if not candidates:
        print("\n  No recorder found automatically.")
        print("  That usually means one of:")
        print("    - this PC is on a different network to the recorder")
        print("    - the recorder is switched off")
        print("    - the recorder's web interface is disabled\n")
        if ask_yes("Do you know the recorder's IP address and want to type it"):
            return ask("Recorder IP (e.g. 192.168.1.108)") or None
        return None

    print("\n  Found:\n")
    for i, (ip, ports) in enumerate(candidates, 1):
        hint = ""
        if 37777 in ports:
            hint = "  (Dahua family)"
        elif 34567 in ports:
            hint = "  (Xiongmai - NOT SUPPORTED)"
        elif 554 in ports:
            hint = "  (streams video)"
        print(f"    {i}. {ip}    ports {', '.join(str(p) for p in ports)}{hint}")
    print(f"    {len(candidates) + 1}. none of these - type an address myself\n")

    while True:
        pick = ask("Which one", "1")
        if pick.isdigit():
            n = int(pick)
            if 1 <= n <= len(candidates):
                return candidates[n - 1][0]
            if n == len(candidates) + 1:
                return ask("Recorder IP") or None
        print("  Please enter one of the numbers above.")


def try_connect(ip: str, user: str, password: str):
    """
    Attempt every sensible URL for this address.

    Recorders move their web interface constantly, so trying only :80 and
    declaring failure is how we lost three site visits.
    """
    ports = [80, 8000, 8080, 81, 88, 8081]
    # Put anything already known to be open first.
    try:
        found = [r.port for r in discover.scan(ip, log=lambda m: None)
                 if r.open and r.kind in ("http", "https")]
        ports = found + [p for p in ports if p not in found]
    except Exception:                                     # noqa: BLE001
        pass

    tried = []
    for port in ports:
        url = f"http://{ip}" if port == 80 else f"http://{ip}:{port}"
        print(f"  trying {url} ...", end=" ", flush=True)
        try:
            driver, info = autodetect(url, user, password, timeout=6,
                                      log=lambda m: None)
            print("connected")
            return driver, info, url
        except DriverError as e:
            first = str(e).splitlines()
            reason = "no answer"
            for line in first:
                low = line.lower()
                if "401" in low or "unauthor" in low:
                    reason = "wrong username or password"
                    break
                if "refused" in low:
                    reason = "nothing listening"
                    break
                if "timed out" in low:
                    reason = "no answer"
                    break
            print(reason)
            tried.append((url, reason))
            if reason == "wrong username or password":
                # The address is right; no point trying other ports.
                return None, None, ("auth", url)
    return None, None, ("none", tried)


def run(cfg_path: Path, supabase_url: str, publishable_key: str,
        enrollment_code: str) -> dict | None:
    """
    Walk the whole setup. Returns the settings to save, or None if the
    user gave up. Nothing is written to disk here.
    """
    print(BANNER)

    me = discover.local_ipv4()
    if me:
        print(f"  This PC is {me}\n")
    else:
        print("  Could not read this PC's network address.\n")

    ip = choose_recorder()
    if not ip:
        print("\n  Setup stopped - no recorder address.")
        return None

    print(f"\n  Using recorder at {ip}")
    print("  Now the recorder's own login - the same one used to view the")
    print("  cameras on its screen or in its web page.\n")

    driver = info = url = None
    for attempt in range(3):
        user = ask("Username", "admin")
        password = ask("Password")
        if not password:
            print("  A password is required.\n")
            continue

        print()
        driver, info, url = try_connect(ip, user, password)
        if driver:
            break

        if isinstance(url, tuple) and url[0] == "auth":
            print(f"\n  The recorder at {url[1]} rejected that login.")
            if attempt < 2:
                print("  Try again.\n")
            continue

        print("\n  Could not reach a web interface on that address.")
        print("  On the recorder itself, check:")
        print("    Main Menu > Network > Port  ->  is HTTP enabled?")
        print("                                    what is the HTTP port?\n")
        if ask_yes("Try a different address"):
            ip = ask("Recorder IP") or ip
            continue
        return None

    if not driver:
        print("\n  Setup stopped - could not connect.")
        return None

    # Prove it properly before writing anything.
    print(f"\n  Connected.")
    print(f"    make      {info.vendor}")
    print(f"    model     {info.model or 'unknown'}")
    print(f"    firmware  {info.firmware or 'unknown'}")
    print(f"    driver    {driver.name}")

    try:
        channels = driver.list_channels()
        print(f"\n  {len(channels)} camera(s):")
        for c in channels:
            print(f"    {c.channel:>3}  {c.name or 'unnamed'}")
    except DriverError as e:
        print(f"\n  Connected, but could not list cameras: "
              f"{str(e).splitlines()[0][:120]}")
        channels = []

    if not driver.verified_against_hardware:
        print("\n  NOTE: this recorder model has not been confirmed working")
        print("  before. It connected, which is the hard part, but watch the")
        print("  first day's events and report anything odd.")

    driver.close()

    if not ask_yes("\n  Save this and start monitoring"):
        return None

    code = enrollment_code
    if not code:
        print("\n  Last thing: the one-time setup code you were given.")
        code = ask("Setup code")
        if not code:
            print("  A setup code is required to link this site.")
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
    """
    Write watchlog.ini as UTF-8 with NO byte order mark.

    Explicitly not using the default encoding: a BOM here is what made the
    agent refuse its own config file earlier, and this file is the one an
    installer is most likely to open in Notepad later.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["; WatchLog - written by the setup wizard.",
             "; Contains this site's recorder password. Do not share.",
             "",
             "[watchlog]"]
    for k, v in values.items():
        lines.append(f"{k} = {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  Saved to {path}")
