"""Recorder-native analytics metadata and on-demand evidence retrieval.

This module wraps the existing vendor drivers rather than replacing their
proven event/identity logic. Native smart events are labelled so the packaged
agent can trust the recorder's own SMD/IVS classification instead of running a
second person/vehicle gate over the same event.

Dahua clip retrieval is deliberately on-demand and bounded. It uses the
vendor's local HTTP ``loadfile.cgi`` playback/export endpoint and returns the
native DAV payload. No continuous video is uploaded and no recorder credential
leaves the site PC.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re

import requests
from requests.auth import HTTPBasicAuth

from .base import DriverError, Event, explain
from .dahua import DahuaDriver
from .hikvision import HikvisionDriver

_DAHUA_NATIVE_AI = {
    "SmartMotionHuman","SmartMotionVehicle","CrossLineDetection",
    "CrossRegionDetection","LeftDetection","TakenAwayDetection",
}
_HIK_NATIVE_AI = {
    "peopledetection","vehicledetection","linedetection","fielddetection",
    "regionexiting","regionentrance",
}

CLIP_MAX_BYTES = 32 * 1024 * 1024
CLIP_CONNECT_TIMEOUT = 5
CLIP_READ_TIMEOUT = 90


def _native(ev: Event | None, vendor: str, code: str) -> Event | None:
    if ev is None:
        return None
    payload = dict(ev.payload or {})
    payload.update({
        "source":"recorder_native_ai","native_ai":True,
        "native_vendor":vendor,"native_code":code,
    })
    return replace(ev,payload=payload)


class NativeDahuaDriver(DahuaDriver):
    """Dahua driver with explicit SMD/IVS provenance and bounded DAV export."""

    name = DahuaDriver.name

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._clock_offset_seconds: float | None = None

    def _parse_line(self,line: str) -> Event | None:
        ev=super()._parse_line(line)
        if ev is None:return None
        head=line.partition(";data=")[0]
        code=""
        for part in head.split(";"):
            key,_,value=part.partition("=")
            if key.strip()=="Code":
                code=value.strip();break
        if code in _DAHUA_NATIVE_AI:return _native(ev,"dahua",code)
        payload=dict(ev.payload or {});payload.setdefault("source","recorder_event")
        return replace(ev,payload=payload)

    def capabilities(self) -> dict:
        data=super().capabilities()
        data["event_source"]="recorder"
        has_native=False
        for row in data.get("channels") or []:
            for analytic in row.get("analytics") or []:
                analytic["source"]="recorder"
                if analytic.get("key") in ("human_vehicle","line_crossing","intrusion"):
                    analytic["native_ai"]=True
                    if analytic.get("supported"):
                        has_native=True
        data["native_ai"]=has_native
        # Candidate is not validation. Exact model/firmware must pass the
        # field clip test before WatchLog calls footage retrieval supported.
        data["incident_footage"]={
            "mode":"on_demand","format":"dav","candidate":True,"validated":False,
        }
        return data

    def _device_clock_offset(self) -> float:
        if self._clock_offset_seconds is not None:return self._clock_offset_seconds
        try:
            text=self._get("/cgi-bin/global.cgi?action=getCurrentTime")
            match=re.search(r"result\s*=\s*(\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}:\d{2})",text)
            if match:
                local_naive=datetime.strptime(match.group(1),"%Y-%m-%d %H:%M:%S")
                utc_naive=datetime.now(timezone.utc).replace(tzinfo=None)
                self._clock_offset_seconds=(local_naive-utc_naive).total_seconds()
                return self._clock_offset_seconds
        except Exception:
            pass
        local_now=datetime.now().replace(tzinfo=None)
        utc_now=datetime.now(timezone.utc).replace(tzinfo=None)
        self._clock_offset_seconds=(local_now-utc_now).total_seconds()
        return self._clock_offset_seconds

    def _recorder_time(self,value: datetime) -> str:
        if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
        utc_naive=value.astimezone(timezone.utc).replace(tzinfo=None)
        local=utc_naive+timedelta(seconds=self._device_clock_offset())
        return local.strftime("%Y-%m-%d %H:%M:%S")

    def get_clip(self,channel: str,start: datetime,end: datetime) -> bytes | None:
        """Export a bounded recorder-native DAV segment for one incident.

        Firmware families differ in how precisely ``loadfile.cgi`` trims an
        interval. Responses beyond the pilot cap are aborted instead of
        consuming uncontrolled site uplink/storage.
        """
        try:channel_no=int(str(channel))
        except (TypeError,ValueError):raise DriverError("invalid Dahua channel for incident footage")
        if end<=start:raise DriverError("invalid incident footage time window")
        url=self.base_url+"/cgi-bin/loadfile.cgi"
        params={
            "action":"startLoad","channel":channel_no,
            "startTime":self._recorder_time(start),"endTime":self._recorder_time(end),
            "subtype":0,
        }
        try:
            response=self.s.get(url,params=params,stream=True,timeout=(CLIP_CONNECT_TIMEOUT,CLIP_READ_TIMEOUT))
            if response.status_code==401:
                self.s.auth=HTTPBasicAuth(self.username,self.password)
                response.close()
                response=self.s.get(url,params=params,stream=True,timeout=(CLIP_CONNECT_TIMEOUT,CLIP_READ_TIMEOUT))
        except requests.RequestException as exc:
            raise DriverError(f"incident footage export: {explain(exc)}") from exc
        try:
            if response.status_code in (404,405,501):return None
            if response.status_code>=400:raise DriverError(f"incident footage export: HTTP {response.status_code}")
            chunks=[];total=0
            for chunk in response.iter_content(chunk_size=256*1024):
                if not chunk:continue
                total+=len(chunk)
                if total>CLIP_MAX_BYTES:raise DriverError("incident footage exceeds the 32 MiB pilot limit")
                chunks.append(chunk)
            data=b"".join(chunks)
            if not data:return None
            head=data[:256].lower()
            if b"error" in head or b"<!doctype html" in head or b"<html" in head:return None
            return data
        finally:
            response.close()


class NativeHikvisionDriver(HikvisionDriver):
    """Hikvision wrapper that labels recorder-side smart analytics provenance."""

    name=HikvisionDriver.name

    def _parse_alert(self,raw: bytes) -> Event | None:
        ev=super()._parse_alert(raw)
        if ev is None:return None
        raw_type=str((ev.payload or {}).get("eventType") or "").lower()
        if raw_type in _HIK_NATIVE_AI:return _native(ev,"hikvision",raw_type)
        payload=dict(ev.payload or {});payload.setdefault("source","recorder_event")
        return replace(ev,payload=payload)

    def capabilities(self) -> dict:
        data=super().capabilities();data["event_source"]="recorder"
        has_native=False
        for row in data.get("channels") or []:
            for analytic in row.get("analytics") or []:
                analytic["source"]="recorder"
                if analytic.get("key") in ("line_crossing","intrusion"):
                    analytic["native_ai"]=True
                    if analytic.get("supported"):has_native=True
        data["native_ai"]=has_native
        data["incident_footage"]={"mode":"on_demand","candidate":False,"validated":False}
        return data
