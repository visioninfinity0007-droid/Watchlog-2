"""Archive / historical-scan runtime (workstream 3).

Orchestrates the agent-side execution of migrations 0051/0055 while keeping the three
provenance classes strictly separate and honest:
    live WatchLog observation  |  recorder-native historical event  |  archive-reprocessed AI.

Per claimed scan: locate + retrieve BOUNDED recorder footage for the requested cameras/range,
run the SAME analytics engine OFFLINE over the recovered frames, and post each candidate — every
recorded result carries the fixed 'Recovered from recorder archive' provenance label (enforced by
0051) and detail.source='archive'.

Actual footage retrieval (recorder playback / driver.get_clip) is the least-consistent vendor API
and is NOT hardware-validated. The retriever is injected so this contract is testable; a real run
reports the scan 'failed' with an honest reason rather than pretending WatchLog AI watched an
offline period live. Never fabricates a recovered result for footage it could not read.
"""

from __future__ import annotations


class ArchiveRuntime:
    def __init__(self, *, cloud, state, retrieve_frames=None, analyze=None, log=lambda _m: None):
        # retrieve_frames(camera_id, from_ts, to_ts) -> iterable of (jpeg_bytes, timestamp) | None
        #   returning None means "could not retrieve" (honest), NOT "no events".
        # analyze(camera_id, jpeg, ts, rule_ids) -> list of {result_type, recovered_at, confidence?}
        self.cloud = cloud
        self.state = state
        self.retrieve = retrieve_frames
        self.analyze = analyze
        self.log = log

    def _agent(self):
        return {"p_agent_id": self.state["agent_id"], "p_agent_key": self.state["agent_key"]}

    def poll_and_process(self, limit: int = 1) -> list:
        scans = self.cloud.call("wl_agent_claim_archive_scans", **self._agent(), p_limit=limit) or []
        return [self._process(s) for s in scans]

    def _process(self, scan) -> dict:
        scan_id = scan["scan_id"]
        total = 0
        missing = []
        try:
            for cam in scan.get("camera_ids") or []:
                frames = self.retrieve(cam, scan["from_ts"], scan["to_ts"]) if self.retrieve else None
                if frames is None:
                    missing.append(cam)                       # truthful: retrieval unavailable
                    continue
                for jpeg, ts in frames:
                    cands = self.analyze(cam, jpeg, ts, scan.get("rule_ids")) if self.analyze else []
                    for cand in cands or []:
                        self.cloud.call("wl_agent_record_archive_result", **self._agent(),
                                        p_scan_id=scan_id, p_camera_id=cam,
                                        p_result_type=cand["result_type"],
                                        p_recovered_at=cand["recovered_at"],
                                        p_confidence=cand.get("confidence"),
                                        p_detail={"source": "archive"})
                        total += 1
            if missing and total == 0:
                status, err = "failed", ("recorder footage retrieval unavailable "
                                         "(requires future agent release / hardware validation)")
            elif missing:
                status, err = "complete", f"partial: retrieval unavailable for {len(missing)} camera(s)"
            else:
                status, err = "complete", None
            self.cloud.call("wl_agent_set_archive_scan_status", **self._agent(),
                            p_scan_id=scan_id, p_status=status, p_error=err, p_stats={"candidates": total})
        except Exception as e:                                # noqa: BLE001
            try:
                self.cloud.call("wl_agent_set_archive_scan_status", **self._agent(),
                                p_scan_id=scan_id, p_status="failed", p_error=str(e)[:200], p_stats={})
            except Exception:                                 # noqa: BLE001
                pass
            return {"scan_id": scan_id, "status": "failed", "candidates": 0}
        return {"scan_id": scan_id, "status": status, "candidates": total}
