"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../lib/supabase";
import { requireTenant } from "../shell";
import { rememberSite, selectedSiteId, withSite } from "../site-context";
import {
  OwnerPage,
  SiteSelect,
  Lead,
  Section,
  Status,
  Ledger,
  RailSection,
  Stat,
  Figure,
  Summary,
  Empty,
  Loading,
  Notice,
  AskLinks,
  ratioPct,
} from "../owner/ui";
import styles from "./cameras.module.css";
import { atMostOneRecorder, latestEventFor, latestEventIndex } from "./camera-events";
import { recorderImpact } from "../site-health/recorder-impact";

function human(v) {
  return String(v || "Not verified")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function eventLabel(v) {
  if (!v) return "Camera event";
  return String(v)
    .replace(/^analytic_/, "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase());
}

function ago(ts) {
  if (!ts) return "No recent preview";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function when(ts, timeZone) {
  if (!ts) return "";
  try {
    return (
      new Intl.DateTimeFormat("en-PK", {
        timeZone: timeZone || "Asia/Karachi",
        day: "numeric",
        month: "short",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      }).format(new Date(ts)) + " · site time"
    );
  } catch {
    return String(ts);
  }
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
      return { label: "Healthy", tone: "ok" };
    case "degraded":
      return { label: "Needs attention", tone: "warn" };
    case "offline":
      return { label: "Offline", tone: "bad" };
    default:
      return { label: "Not verified", tone: "unknown" };
  }
}

function recordingView(state) {
  switch (String(state || "unknown").toLowerCase()) {
    case "recording":
      return { label: "Recording confirmed", tone: "ok" };
    case "not_recording":
      return { label: "Not recording", tone: "warn" };
    case "storage_fault":
      return { label: "Recording needs attention", tone: "warn" };
    default:
      return { label: "Recording not verified", tone: "unknown" };
  }
}

// One overall state per camera: a confirmed fault outranks attention, attention outranks
// "not verified", and only a camera that is both healthy and confirmed recording reads as ok.
function cameraTone(c) {
  const health = healthView(c.health_state).tone;
  const recording = recordingView(c.recording_state).tone;
  if (health === "bad") return "bad";
  if (health === "warn" || recording === "warn") return "warn";
  if (health === "ok" && recording === "ok") return "ok";
  return "unknown";
}
const toneRank = { bad: 0, warn: 1, unknown: 2, ok: 3 };

function cameraName(c) {
  return c.name || `Camera ${c.channel}`;
}

function recorderView(row) {
  const state = String(row?.state || "unknown").toLowerCase();
  const issue = String(row?.issue || "").toLowerCase();
  if (state === "healthy") return { label: "Available", tone: "ok" };
  if (state === "offline") return { label: "Unavailable", tone: "bad" };
  if (state === "attention" && issue === "storage") return { label: "Storage needs attention", tone: "warn" };
  if (state === "attention" && issue === "sign_in") return { label: "Sign-in needs attention", tone: "warn" };
  if (state === "attention") return { label: "Needs attention", tone: "warn" };
  return { label: "Not verified", tone: "unknown" };
}

export default function CustomerCameraView() {
  const [email, setEmail] = useState("");
  const [sites, setSites] = useState([]);
  const [siteId, setSiteId] = useState("");
  const [ctx, setCtx] = useState(null);
  const [recorderSummary, setRecorderSummary] = useState(null);
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
    setRecorderSummary(null);
    setShots({});
    shotsRef.current = {};
    (async () => {
      const sb = supabase();
      const [contextResult, restaurantResult, recorderResult] = await Promise.all([
        sb.rpc("wl_ai_context", { p_site_id: siteId }),
        sb.rpc("wl_restaurant_site_config", { p_site_id: siteId }),
        sb.rpc("wl_my_site_recorders", { p_site_id: siteId }),
      ]);
      if (!live) return;
      if (contextResult.error) {
        setError(say(contextResult.error));
        return;
      }
      setCtx(contextResult.data || null);
      setRecorderSummary(
        !recorderResult.error ? recorderResult.data || null : null,
      );
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

  function chooseSite(id) {
    setSiteId(id);
    rememberSite(id, sites.find((x) => String(x.id) === String(id))?.name || "");
    history.replaceState(null, "", "/control-room/?site=" + encodeURIComponent(id));
  }

  const site = sites.find((s) => s.id === siteId);
  const tz = site?.timezone;
  const faults = ctx?.faults || [];
  const cameras = useMemo(
    () =>
      [...(ctx?.cameras || [])]
        .filter((c) => c.monitor)
        .sort(
          (a, b) =>
            toneRank[cameraTone(a)] - toneRank[cameraTone(b)] ||
            (a.channel || 0) - (b.channel || 0),
        ),
    [ctx],
  );
  const cameraIds = useMemo(() => cameras.map((c) => c.id), [cameras]);
  const recorderRows = recorderSummary?.recorders || [];
  const multiRecorder = recorderRows.length > 1;
  const recorderById = useMemo(
    () => new Map(recorderRows.map((row) => [String(row.id), row])),
    [recorderSummary],
  );
  const recorderByCamera = useMemo(() => {
    const map = new Map();
    for (const row of recorderRows) {
      for (const cameraId of row.camera_ids || []) {
        map.set(String(cameraId), String(row.id));
      }
    }
    return map;
  }, [recorderSummary]);
  // Recorder root cause with the System Health rules (MNVR-068): only a recorder issue that stops
  // WatchLog observing its cameras outranks camera faults, and the affected cameras are the ones the
  // root cause explains, never every camera behind a recorder with a storage issue.
  const impact = recorderImpact({ cams: cameras, faults, recorderRows });
  const recorderIssues = impact.recorderIssues;
  const recorderHealthy = recorderRows.filter(
    (row) => String(row.state || "").toLowerCase() === "healthy",
  ).length;

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

    // Realtime normally resolves this as soon as the site connection uploads the new
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
  const healthy = cameras.filter(
    (c) => String(c.health_state || "").toLowerCase() === "operational",
  ).length;
  const offline = cameras.filter((c) => cameraTone(c) === "bad");
  const attention = cameras.filter((c) => cameraTone(c) === "warn");
  const unverified = cameras.filter((c) => cameraTone(c) === "unknown");
  const needsAttention = offline.length + attention.length;
  const connected = Boolean(ctx?.connectivity?.agent_online);
  const seen = Boolean(ctx?.connectivity?.last_seen);
  const coverage = ratioPct(ctx?.coverage?.coverage_ratio);

  const roleByCamera = useMemo(() => {
    const map = new Map();
    for (const row of restaurantConfig?.cameras || []) {
      map.set(String(row.camera_id), row.role || "");
    }
    return map;
  }, [restaurantConfig]);

  // Latest camera event per camera: camera UUID first, then recorder+channel. Channel alone only
  // for an event without either id (context before 0152) on a site with one recorder at most.
  const latestEventByCamera = useMemo(() => latestEventIndex(ctx?.recent_events), [ctx]);
  const channelFallback = atMostOneRecorder(recorderRows, ctx);

  const cameraGroups = useMemo(() => {
    if (multiRecorder) {
      const groups = recorderRows.map((row) => {
        const view = recorderView(row);
        return {
          key: "recorder-" + row.id,
          label: row.name,
          description: view.label,
          recorder: row,
          cameras: cameras.filter((camera) =>
            String(camera.recorder_id || recorderByCamera.get(String(camera.id)) || "") === String(row.id)
          ),
        };
      }).filter((group) => group.cameras.length);
      const assigned = new Set(groups.flatMap((group) => group.cameras.map((camera) => String(camera.id))));
      const unassigned = cameras.filter((camera) => !assigned.has(String(camera.id)));
      if (unassigned.length) {
        groups.push({
          key: "recorder-unverified",
          label: "Recorder not verified",
          description: "WatchLog has not verified which recorder these cameras belong to.",
          cameras: unassigned,
        });
      }
      return groups;
    }
    if (!restaurantConfig) {
      return [{ key: "all", label: "Monitored cameras", description: "", cameras }];
    }
    const defs = [
      { key: "dining", label: "Customer areas", description: "Dining-floor views for diner and table activity.", roles: new Set(["dining_floor"]) },
      { key: "operations", label: "Service operations", description: "Kitchen, service handoff, cash-counter and service-access views.", roles: new Set(["kitchen","service_handoff","cash_counter","service_access"]) },
      { key: "security", label: "Management & security", description: "Office and management views for security context.", roles: new Set(["office_security"]) },
      { key: "other", label: "Other cameras", description: "Cameras without a restaurant area.", roles: new Set([]) },
    ];
    const groups = defs.map((d) => ({ ...d, cameras: [] }));
    for (const camera of cameras) {
      const role = roleByCamera.get(String(camera.id)) || "";
      let target = groups.find((g) => g.roles.has(role));
      if (!target) target = groups[groups.length - 1];
      target.cameras.push(camera);
    }
    return groups.filter((g) => g.cameras.length);
  }, [cameras, restaurantConfig, roleByCamera, multiRecorder, recorderSummary]);

  const healthHref = withSite("/site-health/", siteId);
  const names = (list) =>
    list.slice(0, 3).map(cameraName).join(" · ") +
    (list.length > 3 ? ` and ${list.length - 3} more` : "");

  // One conclusion, driven only by governed camera and connection state.
  // A storage issue (non-blocking) is mentioned alongside camera faults, never instead of them.
  const storageNote = recorderIssues.length && !impact.blockingIssues.length
    ? ` · Storage needs attention on ${recorderIssues.map((row) => row.name || "the recorder").join(", ")}`
    : "";
  let lead = null;
  if (ctx && cameras.length) {
    if (!connected && !seen) {
      lead = { tone: "unknown", title: "This site has not connected yet", body: "Camera states appear once the site connection is online.", action: <a className="ow-btn" href={withSite("/setup/", siteId)}>Continue setup</a> };
    } else if (!connected) {
      lead = { tone: "bad", title: "Site connection lost · camera states may be out of date", body: "Recent views and events may be missing until the site reconnects.", action: <a className="ow-btn" href={healthHref}>Check monitoring</a> };
    } else if (impact.blockingIssues.length) {
      const affected = impact.recorderIssueCameraIds.size;
      const unavailable = recorderIssues.filter((row) => String(row.state || "").toLowerCase() === "offline").length;
      // Offline cameras the failing recorder does not explain (behind another recorder) stay named.
      const elsewhere = offline.filter((c) => !impact.recorderFor(c));
      lead = {
        tone: unavailable || elsewhere.length ? "bad" : "warn",
        title: `${recorderIssues.length} recorder${recorderIssues.length === 1 ? " needs" : "s need"} attention`,
        body: [
          affected ? `${affected} camera${affected === 1 ? " is" : "s are"} affected.` : "",
          elsewhere.length ? `Also offline: ${names(elsewhere)}.` : "",
          "Open System Health for the recorder-level cause.",
        ].filter(Boolean).join(" "),
        action: <a className="ow-btn" href={healthHref}>Check monitoring</a>,
      };
    } else if (offline.length) {
      lead = { tone: "bad", title: `${offline.length} camera${offline.length === 1 ? " is" : "s are"} offline · evidence may be missing`, body: names(offline) + storageNote, action: <a className="ow-btn" href={healthHref}>Check monitoring</a> };
    } else if (attention.length || faults.length) {
      const n = attention.length || faults.length;
      lead = { tone: "warn", title: `${n} camera${n === 1 ? " needs" : "s need"} attention`, body: (attention.length ? names(attention) : "Open System Health for the affected cameras.") + storageNote, action: <a className="ow-btn" href={healthHref}>Check monitoring</a> };
    } else if (recorderIssues.length) {
      // Only non-blocking (storage) recorder issues remain: WatchLog still observes the cameras.
      const affected = impact.recorderIssueCameraIds.size;
      lead = {
        tone: "warn",
        title: `${recorderIssues.length} recorder${recorderIssues.length === 1 ? " needs" : "s need"} attention`,
        body: (affected ? `${affected} camera${affected === 1 ? " is" : "s are"} affected.` : "WatchLog is still observing the cameras.") + " Open System Health for the recorder-level cause.",
        action: <a className="ow-btn" href={healthHref}>Check monitoring</a>,
      };
    } else if (unverified.length) {
      lead = { tone: "unknown", title: `${healthy} of ${cameras.length} cameras confirmed healthy`, body: `Recording confirmed on ${recording} of ${cameras.length}. Anything WatchLog cannot verify stays marked Not verified.` };
    } else {
      lead = { tone: "ok", title: `All ${cameras.length} cameras healthy · recording confirmed`, body: coverage === null ? "Monitoring coverage for today is not verified yet." : `Monitoring ${coverage}% verified today.` };
    }
  }

  const rail = ctx ? (
    <>
      <RailSection label="Monitoring coverage" action={<a href={healthHref}>Health</a>}>
        {coverage === null ? <Figure value="—" unit="not verified yet" /> : <Figure value={coverage + "%"} unit="verified today" />}
        <div style={{ marginTop: 10 }}>
          <Ledger ratio={coverage === null ? null : coverage / 100} classes={ctx?.coverage?.classes} />
        </div>
        <div style={{ marginTop: 8 }}>
          <Stat label="Site connection" value={<Status tone={connected ? "ok" : seen ? "bad" : "unknown"}>{connected ? "Connected" : seen ? "Disconnected" : "Not connected yet"}</Status>} />
        </div>
      </RailSection>
      <RailSection label="Cameras">
        {recorderRows.length ? <Stat
          label={multiRecorder ? "Recorders available" : "Recorder"}
          value={multiRecorder
            ? `${recorderHealthy} of ${recorderRows.length}`
            : <Status tone={recorderView(recorderRows[0]).tone}>{recorderView(recorderRows[0]).label}</Status>}
        /> : null}
        <Stat label="Monitored" value={String(cameras.length)} />
        <Stat label="Confirmed healthy" value={cameras.length ? `${healthy} of ${cameras.length}` : null} />
        <Stat label="Needs attention" value={String(needsAttention)} />
        <Stat label="Not verified" note="health or recording" value={String(unverified.length)} muted={!unverified.length} />
        <Stat label="Recording confirmed" value={cameras.length ? `${recording} of ${cameras.length}` : null} />
      </RailSection>
      <RailSection label="Evidence">
        <a className="ow-rail-link" href={withSite("/incidents/evidence/", siteId)}><span>Camera event history</span><i>Open</i></a>
        <a className="ow-rail-link" href={withSite("/archive/", siteId)}><span>Saved video</span><i>Open</i></a>
        <a className="ow-rail-link" href={withSite("/site-control/", siteId)}><span>Camera settings</span><i>Open</i></a>
      </RailSection>
      <RailSection label="Ask WatchLog">
        <AskLinks siteId={siteId} prompts={["Are all my cameras recording right now?", "Which cameras need attention, and why?", "What did the cameras show most recently?"]} />
      </RailSection>
    </>
  ) : null;

  const summary = ctx && cameras.length ? (
    <Summary items={[
      ...(multiRecorder ? [{ value: `${recorderHealthy}/${recorderRows.length}`, label: "Recorders available", muted: recorderHealthy !== recorderRows.length }] : []),
      { value: `${healthy}/${cameras.length}`, label: "Cameras healthy" },
      { value: String(needsAttention), label: "Need attention" },
      { value: coverage === null ? "Not verified" : coverage + "%", label: "Coverage today", muted: coverage === null, ledger: coverage === null ? null : coverage / 100 },
    ]} />
  ) : null;

  function renderCamera(c) {
    const shot = shots[c.id];
    const health = healthView(c.health_state);
    const recordingState = recordingView(c.recording_state);
    const role = roleByCamera.get(String(c.id));
    const purpose = role ? human(role) : c.purpose ? human(c.purpose) : "Purpose not set";
    const recorderId = String(c.recorder_id || recorderByCamera.get(String(c.id)) || "");
    const latest = latestEventFor(latestEventByCamera, c, { recorderId, channelFallback, cameras: ctx?.cameras });
    const evidenceHref = latest?.event_id
      ? withSite("/incidents/evidence/?event=" + encodeURIComponent(latest.event_id), siteId)
      : withSite("/incidents/evidence/", siteId);
    return (
      <article className={styles.cam} key={c.id} aria-label={cameraName(c)}>
        <i className={`${styles.tick} ${styles[cameraTone(c)] || ""}`} aria-hidden="true" />
        <div className={`${styles.thumb} ${shot?.image ? "" : styles.none}`}>
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
        <div className={styles.main}>
          <h3>{cameraName(c)}</h3>
          <p className={`${styles.purpose} ${role || c.purpose ? "" : styles.unset}`}>{purpose}</p>
          <div className={styles.states}>
            <Status tone={health.tone}>{health.label}</Status>
            <Status tone={recordingState.tone}>{recordingState.label}</Status>
          </div>
          <p className={styles.evidence}>
            {latest ? (
              <span title={when(latest.device_ts, tz)}>
                Latest camera event · {eventLabel(latest.event_type)} · {ago(latest.device_ts)}
                {latest.recovered ? " · recovered from saved video" : ""}
              </span>
            ) : (
              <span>No recent camera event listed</span>
            )}
          </p>
          <div className="ow-row-meta">
            <span title={shot?.captured ? when(shot.captured, tz) : undefined}>
              {shot?.captured
                ? `Recent view ${ago(shot.captured)}`
                : "No recent view"}
            </span>
          </div>
        </div>
        <div className={styles.act}>
          <a href={evidenceHref}>View evidence</a>
          <button
            type="button"
            className="ow-btn small quiet"
            aria-busy={busy === c.id}
            disabled={busy === c.id}
            onClick={() => refresh(c)}
          >
            {busy === c.id ? "Refreshing…" : "Refresh view"}
          </button>
        </div>
      </article>
    );
  }

  return (
    <OwnerPage
      active="Control Room"
      email={email}
      siteId={siteId}
      kicker={["Cameras & Evidence", tz ? "Site time " + tz.replace(/^.*\//, "").replace(/_/g, " ") : null]}
      title={`Cameras & evidence at ${site?.name || "this site"}`}
      actions={
        <>
          <SiteSelect sites={sites} value={siteId} onChange={chooseSite} />
          <a className="ow-btn quiet" href={withSite("/incidents/evidence/", siteId)}>Camera evidence</a>
          <a className="ow-btn quiet" href={withSite("/archive/", siteId)}>Saved video</a>
        </>
      }
      rail={rail}
      summary={summary}
    >
      {error && <Notice tone="bad">{error}</Notice>}

      {!ctx ? (
        <Loading label="Checking this site's cameras" />
      ) : cameras.length === 0 ? (
        <Empty
          title="No cameras are selected for monitoring yet."
          action={<a className="ow-btn quiet small" href={withSite("/setup/", siteId)}>Continue Guided Setup</a>}
        >
          Choose the cameras to monitor in Guided Setup.
        </Empty>
      ) : (
        <>
          {lead && <Lead tone={lead.tone} title={lead.title} body={lead.body} action={lead.action} />}

          {cameraGroups.map((group, gi) => (
            <Section
              key={group.key}
              first={gi === 0}
              title={group.label}
              count={group.cameras.length}
              note={group.description || undefined}
            >
              <div className={styles.list}>
                {group.cameras.map(renderCamera)}
              </div>
            </Section>
          ))}

          <Section title="Ask WatchLog" className="ow-narrow-only">
            <AskLinks siteId={siteId} prompts={["Are all my cameras recording right now?", "Which cameras need attention, and why?"]} />
          </Section>
        </>
      )}
    </OwnerPage>
  );
}
