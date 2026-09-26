# WatchLog Virtual CCTV Test Lab

This lab exists so the Windows Site Connector can be tested against deterministic
virtual CCTV devices before a build is taken to a customer site.

It is intentionally isolated from production client recorders.

## Topology

The recommended layout is:

- Windows test PC / installer host: `10.77.0.1`
- Ubuntu Hyper-V VM lab NIC: `10.77.0.2`
- Virtual Dahua NVR: `10.77.0.20`
- Virtual Hikvision NVR: `10.77.0.21`
- Virtual ONVIF camera 1: `10.77.0.31`
- Virtual ONVIF camera 2: `10.77.0.32`
- Generic router/web decoy: `10.77.0.50`

All virtual devices are separate macvlan identities. From Windows they look like
real LAN devices, not localhost port forwards. That is important because the
installer's subnet discovery, multi-device filtering and recorder fingerprinting
must be exercised exactly as they are in the field.

Default recorder credentials:

- username: `admin`
- password: `WatchLog123!`

Never reuse these credentials on a real recorder.

## What the simulator currently covers

### Dahua-family NVR

The lab Dahua advertises the same model used in the Al-Khalid field tests:
`DH-XVR1B08-I`.

Implemented paths include:

- Digest authentication
- `magicBox.cgi` identity
- channel inventory / titles
- camera snapshots
- native event stream
- current VideoLoss/VideoBlind state
- storage health
- recording-mode health
- analytics configuration reads
- recorder clock
- archive search
- bounded archive download
- native signature port `37777`

The Dahua SDK port deliberately starts a few seconds after the web server. A
generic web device is already present on the LAN. This reproduces the discovery
failure where the first scan saw a router but missed the recorder signature.

### Hikvision-family NVR

The lab Hikvision advertises `DS-7608NI-Q1`.

Implemented paths include:

- Digest authentication
- ISAPI device identity
- channel inventory
- snapshot endpoints
- motion / line / field analytics reads
- native alert stream
- httpHosts push configuration write/readback
- ContentMgmt archive search/download
- native signature port `8000`

### Generic ONVIF cameras

Two separate camera identities expose:

- WS-Discovery
- ONVIF Device service
- Media service / profiles
- Event service skeleton
- snapshots
- RTSP port presence

The intent is to test WatchLog's generic ONVIF fallback independently from the
vendor-native paths.

## Lab scenarios

Every virtual recorder has a control API on port `9001`.

From the Windows test PC:

```powershell
# Reset Dahua
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario healthy

# Make recorder auth intentionally slow
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario slow-login

# Report disk/storage fault
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario storage-fault

# Make archive search return no footage
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario archive-empty

# Channel 2 VideoLoss / snapshot failure
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario camera-2-offline

# Channel 3 configured not recording
.\testlab\windows\Set-LabScenario.ps1 -Device dahua -Scenario recording-3-off
```

Inject live events:

```powershell
.\testlab\windows\Send-LabEvent.ps1 -Device dahua -Event motion -Channel 1
.\testlab\windows\Send-LabEvent.ps1 -Device dahua -Event person -Channel 2
.\testlab\windows\Send-LabEvent.ps1 -Device hikvision -Event vehicle -Channel 3
.\testlab\windows\Send-LabEvent.ps1 -Device hikvision -Event video-loss -Channel 4
```

## Recommended Hyper-V setup

Use an Ubuntu VM rather than Docker Desktop for the full discovery lab. Docker
Desktop port mappings do not give each virtual NVR its own normal LAN identity.

1. Enable Hyper-V.
2. Create an Ubuntu VM named `WatchLog-Lab` with one normal Internet NIC.
3. Run an elevated PowerShell:

```powershell
.\testlab\windows\Setup-LabNetwork.ps1 -VMName WatchLog-Lab
```

This creates an isolated internal switch named `WatchLogLab`, assigns Windows
`10.77.0.1/24`, attaches a second VM NIC and enables MAC-address spoofing. The
spoofing setting is required because Docker macvlan presents several virtual
device MAC addresses behind the Ubuntu VM.

Inside Ubuntu identify the lab NIC (normally `eth1`, `ens19`, etc.):

```bash
ip link
export LAB_PARENT_IFACE=eth1
```

Install Docker Engine + the Compose plugin, clone this repository, check out the
installer branch, then:

```bash
cd testlab
cp .env.example .env
LAB_PARENT_IFACE=eth1 ./linux/start-lab.sh
```

Back on Windows:

```powershell
.\testlab\windows\Test-Lab.ps1
```

Only after that smoke test passes should a WatchLog installer build be tested.

## Installer acceptance run

For every Windows installer candidate:

1. Start the virtual lab fresh.
2. Confirm all five virtual devices are reachable.
3. Run the installer with the Dahua recorder.
4. Verify automatic discovery finds `10.77.0.20` without Back/Continue cycling.
5. Verify the loading indicator remains visible throughout discovery.
6. Sign in with the correct credentials once.
7. Verify wrong credentials fail quickly and remain retryable.
8. Switch to `slow-login`; confirm the UI continues showing active progress and
   does not falsely terminate before the recorder path completes.
9. Complete installation and verify all four channels enroll.
10. Inject motion/person/vehicle events and verify they reach the WatchLog site.
11. Set `camera-2-offline`; verify camera health changes appropriately.
12. Set `storage-fault`; verify NVR health reports the storage issue.
13. Set `recording-3-off`; verify recording health is not falsely green.
14. Prove archive search/download in healthy mode.
15. Set `archive-empty`; verify WatchLog reports empty archive truthfully.
16. Repeat the same install against the Hikvision NVR.
17. Test generic ONVIF fallback with one virtual camera.
18. Upgrade over the previous installer build.
19. Exercise rollback/failure behavior.
20. Uninstall and confirm the lab PC is clean.

## Cloud safety

There is currently only one Supabase project in the connected account and it is
the live WatchLog project. The virtual-device lab therefore does not automatically
create or mutate cloud data.

For complete portal/cloud acceptance, use either:

- a separate staging Supabase project, preferred; or
- a dedicated WatchLog Lab tenant/site in production with explicit lab naming and
  cleanup rules.

Do not reuse a real client tenant for simulator tests.

## Important boundary

A simulator can validate WatchLog's behavior against known protocol shapes,
timing, failures and state transitions. It cannot prove every firmware quirk of a
physical Dahua/Hikvision recorder. Final release acceptance should therefore be:

1. virtual lab green;
2. one real Dahua smoke test;
3. one real Hikvision smoke test.

That gives fast repeatability without pretending simulated firmware is identical
to every field recorder.
