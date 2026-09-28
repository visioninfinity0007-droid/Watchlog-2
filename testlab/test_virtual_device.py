#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth

ROOT = Path(__file__).resolve().parents[1]
DEVICE = ROOT / "testlab" / "virtual_device.py"


def _wait_port(port: int, timeout: float = 8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"virtual device did not open port {port}")


def _start(kind: str, http: int, control: int, sdk: int, rtsp: int):
    env = dict(os.environ)
    env.update({
        "DEVICE_KIND": kind,
        "DEVICE_NAME": f"CI {kind}",
        "DEVICE_MODEL": "DH-XVR1B08-I" if kind == "dahua" else (
            "DS-7608NI-Q1" if kind == "hikvision" else "WL-ONVIF-CAM-S"
        ),
        "DEVICE_SERIAL": f"CI-{kind}-001",
        "DEVICE_USERNAME": "admin",
        "DEVICE_PASSWORD": "WatchLog123!",
        "DEVICE_CHANNELS": "4" if kind != "onvif" else "1",
        "HTTP_PORT": str(http),
        "CONTROL_PORT": str(control),
        "SDK_PORT": str(sdk),
        "RTSP_PORT": str(rtsp),
        "SDK_START_DELAY": "0",
    })
    proc = subprocess.Popen(
        [sys.executable, str(DEVICE)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_port(http)
    _wait_port(control)
    if sdk:
        _wait_port(sdk)
    return proc


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def test_virtual_dahua_digest_health_and_scenarios():
    p = _start("dahua", 18080, 19001, 13777, 15554)
    try:
        auth = HTTPDigestAuth("admin", "WatchLog123!")
        base = "http://127.0.0.1:18080"
        r = requests.get(base + "/cgi-bin/magicBox.cgi?action=getSystemInfo", auth=auth, timeout=3)
        assert r.status_code == 200
        assert "DH-XVR1B08-I" in r.text
        assert "videoInChannel=4" in r.text

        bad = requests.get(
            base + "/cgi-bin/magicBox.cgi?action=getSystemInfo",
            auth=HTTPDigestAuth("admin", "wrong"), timeout=3,
        )
        assert bad.status_code == 401

        control = "http://127.0.0.1:19001"
        assert requests.post(control + "/scenario/storage-fault", timeout=2).status_code == 200
        storage = requests.get(
            base + "/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo", auth=auth, timeout=3
        )
        assert "Abnormal" in storage.text

        assert requests.post(control + "/scenario/camera-2-offline", timeout=2).status_code == 200
        snap = requests.get(base + "/cgi-bin/snapshot.cgi?channel=2", auth=auth, timeout=3)
        assert snap.status_code == 503

        assert requests.post(control + "/scenario/healthy", timeout=2).status_code == 200
        snap = requests.get(base + "/cgi-bin/snapshot.cgi?channel=1", auth=auth, timeout=3)
        assert snap.status_code == 200
        assert snap.content[:2] == bytes([0xFF, 0xD8])
    finally:
        _stop(p)


def test_virtual_hikvision_identity_inventory_and_snapshot():
    p = _start("hikvision", 18081, 19002, 18000, 15555)
    try:
        auth = HTTPDigestAuth("admin", "WatchLog123!")
        base = "http://127.0.0.1:18081"
        info = requests.get(base + "/ISAPI/System/deviceInfo", auth=auth, timeout=3)
        assert info.status_code == 200
        assert "DS-7608NI-Q1" in info.text

        channels = requests.get(base + "/ISAPI/ContentMgmt/InputProxy/channels", auth=auth, timeout=3)
        assert channels.status_code == 200
        assert channels.text.count("InputProxyChannel") >= 4

        snap = requests.get(base + "/ISAPI/Streaming/channels/101/picture", auth=auth, timeout=3)
        assert snap.status_code == 200
        assert snap.content[:2] == bytes([0xFF, 0xD8])
    finally:
        _stop(p)


def test_virtual_onvif_device_information():
    p = _start("onvif", 18082, 19003, 0, 15556)
    try:
        auth = HTTPDigestAuth("admin", "WatchLog123!")
        body = """<?xml version="1.0"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
          xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
          <s:Body><tds:GetDeviceInformation/></s:Body>
        </s:Envelope>"""
        r = requests.post(
            "http://127.0.0.1:18082/onvif/device_service",
            data=body.encode(), auth=auth,
            headers={"Content-Type": "application/soap+xml"}, timeout=3,
        )
        assert r.status_code == 200
        assert "WL-ONVIF-CAM-S" in r.text
        assert "GetDeviceInformationResponse" in r.text
    finally:
        _stop(p)
