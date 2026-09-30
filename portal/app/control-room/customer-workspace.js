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
  const [restaurantConfig, setRestaurantConfig] = useState(null);
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
      const sb = supabase();
      const [contextResult, restaurantResult] = await Promise.all([
        sb.rpc("wl_ai_context", { p_site_id: siteId }),
        sb.rpc("wl_restaurant_site_config", { p_site_id: siteId }),
      ]);
      if (!live) return;
      if (contextResult.error) {
        setError(say(contextResult.error));
        return;
      }
      setCtx(contextResult.data || null);
      setRestaurantConfig(
        !restaurantResult.error && restaurantResult.data?.enabled === true
          ? restaurantResult.data
          : null,
      );
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

  const roleByCamera = useMemo(() => {
    const map = new Map();
    for (const row of restaurantConfig?.cameras || []) {
      map.set(String(row.camera_id), row.role || "");
    }
    return map;
  }, [restaurantConfig]);

  const cameraGroups = useMemo(() => {
    if (!restaurantConfig) {
      return [{ key: "all", label: "Monitored cameras", description: "Recent camera views from this site.", cameras }];
    }
    const defs = [
      { key: "dining", label: "Customer areas", description: "Dining-floor views used for visible diner and table activity.", roles: new Set(["dining_floor"]) },
      { key: "operations", label: "Service operations", description: "Kitchen, service handoff, cash-counter and service-access views.", roles: new Set(["kitchen","service_handoff","cash_counter","service_access"]) },
      { key: "security", label: "Management & security", description: "Office and management views used for security context.", roles: new Set(["office_security"]) },
      { key: "other", label: "Other cameras", description: "Configured cameras without a restaurant role.", roles: new Set([]) },
    ];
    const groups = defs.map((d) => ({ ...d, cameras: [] }));
    for (const camera of cameras) {
      const role = roleByCamera.get(String(camera.id)) || "";
      let target = groups.find((g) => g.roles.has(role));
      if (!target) target = groups[groups.length - 1];
      target.cameras.push(camera);
    }
    return groups.filter((g) => g.cameras.length);
  }, [cameras, restaurantConfig, roleByCamera]);

  return (
    <div className="shell">
      <Nav active="Control Room" email={email} currentSiteId={siteId} />
      <main className="main">
        <header className="target-page-head">
          <div>
            <div className="target-eyebrow">Cameras &amp; Evidence</div>
            <h1>See the site by business area</h1>
            <p>
              {cameras.length
                ? `${site?.name || "This site"} · ${cameras.length} monitored cameras · ${recording} with recording currently confirmed. Recent views are organized by what they help management understand.`
                : "See the cameras WatchLog is monitoring at this site."}
            </p>
          </div>
          <div className="target-actions">
            <a className={ui.secondaryLink} href={withSite("/incidents/evidence/", siteId)}>
              Camera evidence
            </a>
            <a className={ui.secondaryLink} href={withSite("/archive/", siteId)}>
              Saved video
            </a>
            <a className={ui.secondaryLink} href={withSite("/site-control/", siteId)}>
              Camera settings
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
                    Review the affected cameras below or open System Health for
                    more context.
                  </p>
                  <a
                    className={ui.primaryLink}
                    href={withSite("/site-health/", siteId)}
                  >
                    Open System Health
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
              <div className="evidence-groups">
                {cameraGroups.map((group) => (
                  <section className="evidence-group" key={group.key}>
                    <div className="evidence-group-head">
                      <div>
                        <h2>{group.label}</h2>
                        <p>{group.description}</p>
                      </div>
                      <span>{group.cameras.length} camera{group.cameras.length === 1 ? "" : "s"}</span>
                    </div>
                    <div className="camera-view-grid">
                      {group.cameras.map((c) => {
                        const shot = shots[c.id];
                        const health = healthView(c.health_state);
                        const recordingState = recordingView(c.recording_state);
                        const role = roleByCamera.get(String(c.id));
                        return (
                          <article className="camera-view-card" key={c.id}>
                            <div className="camera-view-media">
                              {shot?.image ? (
                                <img
                                  src={shot.image}
                                  alt={`Recent view from ${c.name || `camera ${c.channel}`}`}
                                />
                              ) : (
                                <span>
                                  {shot === undefined
                                    ? "Loading recent view…"
                                    : "No recent view"}
                                </span>
                              )}
                            </div>
                            <div className="camera-view-body">
                              <div className="camera-view-title">
                                <div>
                                  <b>{c.name || `Camera ${c.channel}`}</b>
                                  <small>{role ? human(role) : human(c.purpose || "general")}</small>
                                </div>
                                <span className={`pill ${health.cls}`}>
                                  {health.label}
                                </span>
                              </div>
                              <div className="camera-view-meta">
                                <span>
                                  {shot?.captured
                                    ? `Recent view ${ago(shot.captured)}`
                                    : "No recent view"}
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
                                {busy === c.id ? "Refreshing…" : "Refresh view"}
                              </button>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
