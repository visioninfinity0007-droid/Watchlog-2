"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { Nav, requireTenant } from "../shell";
import { rememberSite, selectedSiteId, withSite } from "../site-context";
import ui from "../portal.module.css";

function human(v) {
  return String(v || "Not verified")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function ago(ts) {
  if (!ts) return "No recent preview";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function sameCapture(a, b) {
  if (!a || !b) return false;
  const left = Date.parse(a);
  const right = Date.parse(b);
  return Number.isFinite(left) && Number.isFinite(right) && left === right;
}

function healthView(state) {
  switch (String(state || "unknown").toLowerCase()) {
    case "operational":
      return { label: "Healthy", cls: "s-ok" };
    case "degraded":
      return { label: "Needs attention", cls: "s-warn" };
    case "offline":
      return { label: "Offline", cls: "s-bad" };
    default:
      return { label: "Not verified", cls: "s-unk" };
  }
}

function recordingView(state) {
  switch (String(state || "unknown").toLowerCase()) {
    case "recording":
      return { label: "Recording confirmed", cls: "s-ok" };
    case "not_recording":
      return { label: "Not recording", cls: "s-warn" };
    case "storage_fault":
      return { label: "Recording needs attention", cls: "s-warn" };
    default:
      return { label: "Recording not verified", cls: "s-unk" };
  }
}

export default function CustomerCameraView() {
  const [email, setEmail] = useState("");
  const [sites, setSites] = useState([]);
  const [siteId, setSiteId] = useState("");
  const [ctx, setCtx] = useState(null);
  const [shots, setShots] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const shotsRef = useRef({});
  const refreshTimers = useRef(new Map());

  useEffect(() => {
    shotsRef.current = shots;
  }, [shots]);

  useEffect(() => {
    return () => {
      for (const timer of refreshTimers.current.values()) {
        window.clearTimeout(timer);
      }
      refreshTimers.current.clear();
    };
  }, []);

  useEffect(() => {
    (async () => {
      const g = await requireTenant();
      if (!g) return;
      setEmail(g.session.user.email || "");
      const r = await supabase().rpc("wl_sites");
      if (r.error) {
        setError(say(r.error));
        return;
      }
      const list = r.data || [];
      const id =
        new URLSearchParams(location.search).get("site") ||
        selectedSiteId() ||
        list[0]?.id ||
        "";
      setSites(list);
      setSiteId(id);
      if (id) rememberSite(id, list.find((x) => x.id === id)?.name || "");
    })();
  }, []);

  useEffect(() => {
    if (!siteId) return;
    let live = true;
    setCtx(null);
    setShots({});
    shotsRef.current = {};
    (async () => {
      const r = await supabase().rpc("wl_ai_context", { p_site_id: siteId });
      if (!live) return;
      if (r.error) {
        setError(say(r.error));
        return;
      }
      setCtx(r.data || null);
      setError("");
    })();
    return () => {
      live = false;
    };
  }, [siteId]);

  const site = sites.find((s) => s.id === siteId);
  const faults = ctx?.faults || [];
  const cameras = useMemo(
    () =>
      [...(ctx?.cameras || [])]
        .filter((c) => c.monitor)
        .sort(
          (a, b) =>
            (a.health_state === "offline" ? 0 : 1) -
              (b.health_state === "offline" ? 0 : 1) ||
            (a.channel || 0) - (b.channel || 0),
        ),
    [ctx],
  );
  const cameraIds = useMemo(() => cameras.map((c) => c.id), [cameras]);

  const loadShot = useCallback(async (cameraId) => {
    if (!cameraId) return false;
    const r = await supabase().rpc("wl_camera_config_snapshot", {
      p_camera_id: cameraId,
    });
    if (r.error) {
      setError(say(r.error));
      return false;
    }

    const next = r.data?.image_b64
      ? {
          image: `data:${r.data.content_type || "image/jpeg"};base64,${r.data.image_b64}`,
          captured: r.data.captured_at || null,
        }
      : false;

    setShots((current) => ({ ...current, [cameraId]: next }));
    setBusy((current) => (current === cameraId ? "" : current));

    const timer = refreshTimers.current.get(cameraId);
    if (timer) {
      window.clearTimeout(timer);
      refreshTimers.current.delete(cameraId);
    }
    return true;
  }, []);

  useEffect(() => {
    let live = true;
    (async () => {
      for (const cameraId of cameraIds) {
        if (!live) return;
        if (shotsRef.current[cameraId] !== undefined) continue;
        await loadShot(cameraId);
      }
    })();
    return () => {
      live = false;
    };
  }, [cameraIds, loadShot]);

  const reconcileSignals = useCallback(
    async (targetSiteId) => {
      if (!targetSiteId || cameraIds.length === 0) return;
      const cameraSet = new Set(cameraIds);
      const { data: signals, error: signalError } = await supabase()
        .from("camera_snapshot_signals")
        .select("camera_id,captured_at")
        .eq("site_id", targetSiteId);

      // The initial RPC load still works if Realtime is temporarily unavailable
      // or if the database migration has not reached this environment yet.
      if (signalError) return;

      const refreshes = [];
      for (const signal of signals || []) {
        if (!cameraSet.has(signal.camera_id)) continue;
        const current = shotsRef.current[signal.camera_id];
        if (
          current === undefined ||
          current === false ||
          !sameCapture(current?.captured, signal.captured_at)
        ) {
          refreshes.push(loadShot(signal.camera_id));
        }
      }
      await Promise.all(refreshes);
    },
    [cameraIds, loadShot],
  );

  useEffect(() => {
    if (!siteId || cameraIds.length === 0) return;

    const sb = supabase();
    let live = true;
    const channel = sb
      .channel(`camera-snapshot-signals:${siteId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "camera_snapshot_signals",
          filter: `site_id=eq.${siteId}`,
        },
        (payload) => {
          if (!live) return;
          const row = payload.new || {};
          if (row.camera_id && cameraIds.includes(row.camera_id)) {
            void loadShot(row.camera_id);
          }
        },
      )
      .subscribe((status) => {
        if (live && status === "SUBSCRIBED") {
          void reconcileSignals(siteId);
        }
      });

    const reconcile = () => {
      if (live) void reconcileSignals(siteId);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") reconcile();
    };

    window.addEventListener("focus", reconcile);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      live = false;
      window.removeEventListener("focus", reconcile);
      document.removeEventListener("visibilitychange", onVisibility);
      void sb.removeChannel(channel);
    };
  }, [cameraIds, loadShot, reconcileSignals, siteId]);

  async function refresh(c) {
    setBusy(c.id);
    setError("");
    const r = await supabase().rpc("wl_request_config_snapshot", {
      p_camera_id: c.id,
    });
    if (r.error) {
      setBusy("");
      setError(say(r.error));
      return;
    }

    const previous = refreshTimers.current.get(c.id);
    if (previous) window.clearTimeout(previous);

    // Realtime normally resolves this as soon as the agent uploads the new
    // preview. This bounded fallback reconciles once in case that event was
    // missed during a network transition.
    const timer = window.setTimeout(() => {
      refreshTimers.current.delete(c.id);
      void loadShot(c.id);
      setBusy((current) => (current === c.id ? "" : current));
    }, 15000);
    refreshTimers.current.set(c.id, timer);
  }

  const recording = cameras.filter(
    (c) => String(c.recording_state || "").toLowerCase() === "recording",
  ).length;

  return (
    <div className="shell">
      <Nav active="Control Room" email={email} currentSiteId={siteId} />
      <main className="main">
        <header className="target-page-head">
          <div>
            <div className="target-eyebrow">Cameras</div>
            <h1>{site?.name || "Site"} cameras</h1>
            <p>
              {cameras.length
                ? `${cameras.length} monitored · ${recording} with recording currently confirmed`
                : "See the cameras WatchLog is monitoring at this site."}
            </p>
          </div>
          <div className="target-actions">
            <a
              className={ui.secondaryLink}
              href={withSite("/control-room/advanced/", siteId)}
            >
              Edit layouts
            </a>
          </div>
        </header>

        {error && <div className="err">{error}</div>}

        {!ctx ? (
          <div className={ui.emptyCard}>Checking this site's cameras…</div>
        ) : (
          <>
            {cameras.length === 0 ? (
              <div className={ui.emptyCard}>
                No cameras are selected for monitoring yet. Continue Guided Setup
                to choose them.
              </div>
            ) : faults.length ? (
              <div className={ui.callout}>
                <span className={ui.statusDot} />
                <div>
                  <strong>
                    {faults.length} item{faults.length === 1 ? "" : "s"} need
                    attention.
                  </strong>
                  <p>
                    Review the affected cameras below or open Site Health for
                    more context.
                  </p>
                  <a
                    className={ui.primaryLink}
                    href={withSite("/site-health/", siteId)}
                  >
                    Open Site Health
                  </a>
                </div>
              </div>
            ) : (
              <div className={ui.callout}>
                <span className={ui.statusDot} />
                <div>
                  <strong>No current camera fault is reported.</strong>
                  <p>
                    Statuses that WatchLog cannot currently verify remain marked
                    as not verified below.
                  </p>
                </div>
              </div>
            )}

            {cameras.length > 0 && (
              <section className="camera-view-grid">
                {cameras.map((c) => {
                  const shot = shots[c.id];
                  const health = healthView(c.health_state);
                  const recordingState = recordingView(c.recording_state);
                  return (
                    <article className="camera-view-card" key={c.id}>
                      <div className="camera-view-media">
                        {shot?.image ? (
                          <img
                            src={shot.image}
                            alt={`Preview from ${c.name || `camera ${c.channel}`}`}
                          />
                        ) : (
                          <span>
                            {shot === undefined
                              ? "Loading preview…"
                              : "No recent preview"}
                          </span>
                        )}
                      </div>
                      <div className="camera-view-body">
                        <div className="camera-view-title">
                          <div>
                            <b>{c.name || `Camera ${c.channel}`}</b>
                            <small>{human(c.purpose || "general")}</small>
                          </div>
                          <span className={`pill ${health.cls}`}>
                            {health.label}
                          </span>
                        </div>
                        <div className="camera-view-meta">
                          <span>
                            {shot?.captured
                              ? `Preview ${ago(shot.captured)}`
                              : "No recent preview"}
                          </span>
                          <span className={`pill ${recordingState.cls}`}>
                            {recordingState.label}
                          </span>
                        </div>
                        <button
                          className="ghost small"
                          disabled={busy === c.id}
                          onClick={() => refresh(c)}
                        >
                          {busy === c.id ? "Refreshing…" : "Refresh preview"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
