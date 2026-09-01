"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { supabase, say } from "../../../lib/supabase";
import { Nav, requireTenant } from "../../shell";
import styles from "../analytics.module.css";

const LABELS = {
  visitor_flow: "Visitor Flow",
  vehicle_flow: "Vehicle Flow",
  boundary_monitoring: "Boundary Monitoring",
  zone_activity: "Zone Activity",
  dwell: "Dwell / Time in Zone",
  after_hours: "After-Hours Activity",
  checkout_activity: "Checkout Activity",
};

const FLEXIBLE_CLASSES = new Set(["vehicle_flow", "boundary_monitoring", "zone_activity", "after_hours"]);

function blankRule() {
  return {
    id: null,
    analyticKey: "visitor_flow",
    name: "Visitor Flow",
    ruleType: "line_crossing",
    classes: ["person"],
    points: [],
    scheduleId: "",
    dwellSeconds: 60,
    sampleSeconds: 2,
    inLabel: "in",
    outLabel: "out",
    severity: "measurement",
    promoteIncident: false,
  };
}

function pointString(points) {
  return points.map((p) => `${(p[0] * 100).toFixed(2)},${(p[1] * 100).toFixed(2)}`).join(" ");
}

function scheduleJson(start, end) {
  return { days: {
    mon: [[start, end]], tue: [[start, end]], wed: [[start, end]],
    thu: [[start, end]], fri: [[start, end]], sat: [], sun: [],
  }};
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

export default function AnalyticsStudio() {
  const [email, setEmail] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [studio, setStudio] = useState(null);
  const [siteId, setSiteId] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [snapshot, setSnapshot] = useState(null);
  const [rule, setRule] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [businessStart, setBusinessStart] = useState("08:00");
  const [businessEnd, setBusinessEnd] = useState("18:00");
  const svgRef = useRef(null);

  const load = useCallback(async (preserve=true) => {
    const guard = await requireTenant();
    if (!guard) return;
    setEmail(guard.session.user.email || "");
    const sb = supabase();
    const [{ data: c, error: ce }, { data: s, error: se }] = await Promise.all([
      sb.rpc("wl_analytics_catalog"), sb.rpc("wl_analytics_studio"),
    ]);
    if (ce || se) { setError(say(ce || se)); return; }
    setCatalog(c); setStudio(s); setError("");
    const sites = s?.sites || [];
    const wantedSite = preserve && sites.some((x) => x.id === siteId) ? siteId : sites[0]?.id || "";
    setSiteId(wantedSite);
    const cams = sites.find((x) => x.id === wantedSite)?.cameras || [];
    const wantedCam = preserve && cams.some((x) => x.id === cameraId) ? cameraId : cams[0]?.id || "";
    setCameraId(wantedCam);
  }, [siteId, cameraId]);

  useEffect(() => { load(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sites = studio?.sites || [];
  const site = sites.find((s) => s.id === siteId) || null;
  const cameras = site?.cameras || [];
  const camera = cameras.find((c) => c.id === cameraId) || null;
  const rules = camera?.rules || [];
  const schedules = site?.schedules || [];
  const canManage = studio?.can_manage !== false;

  const analyticsByKey = useMemo(() => {
    const map = {};
    for (const a of catalog?.analytics || []) map[a.key] = a;
    return map;
  }, [catalog]);

  const recommended = useMemo(() => {
    const sitePack = catalog?.packs?.[site?.site_type] || [];
    const purposePack = catalog?.purpose_recommendations?.[camera?.purpose] || [];
    if (!purposePack.length) return sitePack;
    if (!sitePack.length) return purposePack;
    const intersection = purposePack.filter((key) => sitePack.includes(key));
    return intersection.length ? intersection : purposePack;
  }, [catalog, site?.site_type, camera?.purpose]);

  const loadSnapshot = useCallback(async () => {
    if (!cameraId || !camera?.has_config_snapshot) { setSnapshot(null); return false; }
    const { data, error } = await supabase().rpc("wl_camera_config_snapshot", { p_camera_id: cameraId });
    if (error) { setError(say(error)); return false; }
    if (data?.image_b64) {
      setSnapshot(`data:${data.content_type || "image/jpeg"};base64,${data.image_b64}`);
      return true;
    }
    return false;
  }, [cameraId, camera?.has_config_snapshot]);

  useEffect(() => { loadSnapshot(); }, [loadSnapshot]);

  async function setSiteType(value) {
    if (!canManage) return;
    setBusy(true); setNote("");
    const { error } = await supabase().rpc("wl_set_site_type", { p_site_id: siteId, p_site_type: value });
    if (error) setError(say(error)); else { setNote("Site type updated. Recommendations refreshed."); await load(); }
    setBusy(false);
  }

  async function saveCameraProfile(patch={}) {
    if (!camera || !canManage) return;
    setBusy(true); setNote("");
    const { error } = await supabase().rpc("wl_set_camera_profile", {
      p_camera_id: camera.id,
      p_name: patch.name ?? camera.name,
      p_purpose: patch.purpose ?? camera.purpose ?? "custom",
      p_enabled: patch.enabled ?? camera.analytics_enabled ?? true,
    });
    if (error) setError(say(error)); else { setNote("Camera settings updated."); await load(); }
    setBusy(false);
  }

  async function requestSnapshot() {
    if (!camera || !canManage) return;
    setBusy(true); setNote(""); setError("");
    const { error } = await supabase().rpc("wl_request_config_snapshot", { p_camera_id: camera.id });
    if (error) { setError(say(error)); setBusy(false); return; }
    setNote("Snapshot requested. Waiting for the Site Agent...");
    for (let attempt = 0; attempt < 6; attempt++) {
      await sleep(2500);
      const { data, error: snapError } = await supabase().rpc("wl_camera_config_snapshot", { p_camera_id: camera.id });
      if (snapError) { setError(say(snapError)); break; }
      if (data?.image_b64) {
        setSnapshot(`data:${data.content_type || "image/jpeg"};base64,${data.image_b64}`);
        setNote("Current camera still received. Draw the monitoring line or zone on the image.");
        await load();
        setBusy(false);
        return;
      }
    }
    setNote("The request is still pending. The still will appear after the Site Agent checks in; you can retry without creating a duplicate request.");
    await load();
    setBusy(false);
  }

  function startAnalytic(key) {
    if (!canManage) return;
    const a = analyticsByKey[key];
    if (!a) return;
    const r = blankRule();
    r.analyticKey = key;
    r.name = LABELS[key] || a.label || "Monitoring rule";
    r.ruleType = a.rule_type;
    r.classes = a.classes || [];
    if (key === "after_hours") r.promoteIncident = true;
    setRule(r);
    setError(""); setNote("");
  }

  function editRule(existing) {
    if (!canManage) return;
    const match = (catalog?.analytics || []).find((a) => a.rule_type === existing.rule_type &&
      JSON.stringify([...(a.classes || [])].sort()) === JSON.stringify([...(existing.object_classes || [])].sort()));
    const direction = existing.direction || {};
    setRule({
      id: existing.id,
      analyticKey: existing.analytic_key || match?.key || "boundary_monitoring",
      name: existing.name,
      ruleType: existing.rule_type,
      classes: existing.object_classes || [],
      points: existing.geometry?.points || [],
      scheduleId: existing.schedule_id || "",
      dwellSeconds: existing.dwell_seconds || 60,
      sampleSeconds: Number(existing.sample_seconds || 2),
      inLabel: direction.negative_to_positive || "in",
      outLabel: direction.positive_to_negative || "out",
      severity: existing.severity || "measurement",
      promoteIncident: !!existing.promote_incident,
    });
  }

  async function createBusinessSchedule() {
    if (!site || !canManage) return;
    setBusy(true);
    const { data, error } = await supabase().rpc("wl_upsert_monitoring_schedule", {
      p_id: null, p_site_id: site.id, p_name: "Business hours",
      p_timezone: site.timezone || "Asia/Karachi",
      p_schedule: scheduleJson(businessStart, businessEnd), p_enabled: true,
    });
    if (error) setError(say(error)); else {
      setNote("Business-hours schedule created."); await load();
      if (data?.id) setRule((r) => r ? ({ ...r, scheduleId: data.id }) : r);
    }
    setBusy(false);
  }

  function sceneClick(e) {
    if (!rule || !svgRef.current || !canManage) return;
    if (rule.ruleType === "schedule_activity") return;
    const box = svgRef.current.getBoundingClientRect();
    const p = [Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)),
               Math.max(0, Math.min(1, (e.clientY - box.top) / box.height))];
    setRule((r) => {
      if (!r) return r;
      if (r.ruleType === "line_crossing") return { ...r, points: r.points.length >= 2 ? [p] : [...r.points, p] };
      return { ...r, points: r.points.length >= 8 ? r.points : [...r.points, p] };
    });
  }

  async function saveRule() {
    if (!rule || !camera || !canManage) return;
    const geometryNeeded = ["line_crossing", "zone_entry", "zone_dwell", "occupancy"].includes(rule.ruleType);
    const minPoints = rule.ruleType === "line_crossing" ? 2 : 3;
    if (geometryNeeded && rule.points.length < minPoints) {
      setError(`Draw ${minPoints === 2 ? "two points for the monitoring line" : "at least three points around the monitoring zone"}.`); return;
    }
    if (rule.ruleType === "schedule_activity" && !rule.scheduleId) {
      setError("After-hours monitoring needs a business-hours schedule first."); return;
    }
    if (!rule.classes.length) { setError("Choose at least one object type to measure."); return; }
    setBusy(true); setError(""); setNote("");
    const geometry = geometryNeeded ? {
      type: rule.ruleType === "line_crossing" ? "line" : "polygon", points: rule.points,
    } : {};
    let direction = {};
    if (rule.ruleType === "line_crossing") direction = {
      negative_to_positive: rule.inLabel || "in",
      positive_to_negative: rule.outLabel || "out",
    };
    if (rule.ruleType === "schedule_activity") direction = { schedule_mode: "outside" };
    const { error } = await supabase().rpc("wl_upsert_monitoring_rule_v2", {
      p_id: rule.id,
      p_camera_id: camera.id,
      p_analytic_key: rule.analyticKey,
      p_name: rule.name,
      p_rule_type: rule.ruleType,
      p_object_classes: rule.classes,
      p_geometry: geometry,
      p_direction: direction,
      p_schedule_id: rule.scheduleId || null,
      p_dwell_seconds: rule.ruleType === "zone_dwell" ? Number(rule.dwellSeconds || 60) : null,
      p_sample_seconds: Number(rule.sampleSeconds || 2),
      p_enabled: true,
      p_severity: rule.severity,
      p_promote_incident: !!rule.promoteIncident,
    });
    if (error) setError(say(error)); else {
      setNote("Monitoring rule published. The Site Agent will pull the new version automatically.");
      setRule(null); await load();
    }
    setBusy(false);
  }

  async function deleteRule(id) {
    if (!canManage || !confirm("Delete this monitoring rule? Existing analytics history is kept.")) return;
    setBusy(true);
    const { error } = await supabase().rpc("wl_delete_monitoring_rule", { p_rule_id: id });
    if (error) setError(say(error)); else { setNote("Monitoring rule removed."); await load(); }
    setBusy(false);
  }

  function toggleClass(cls) {
    setRule((r) => !r ? r : ({ ...r,
      classes: r.classes.includes(cls) ? r.classes.filter((x) => x !== cls) : [...r.classes, cls],
    }));
  }

  const purposeOptions = catalog?.purposes || [];
  const siteTypes = catalog?.site_types || [];
  const needsScene = rule && ["line_crossing", "zone_entry", "zone_dwell", "occupancy"].includes(rule.ruleType);
  const canChooseClasses = rule && FLEXIBLE_CLASSES.has(rule.analyticKey);

  return <div className="shell">
    <Nav active="Analytics" email={email} />
    <main className="main">
      <div className={styles.pageHead}>
        <div>
          <div className={styles.subnav}>
            <a href="/analytics/">Overview</a>
            <a className={styles.current} href="/analytics/studio/">Analytics Studio</a>
            <a href="/analytics/schedules/">Schedules</a>
          </div>
          <h1 style={{ marginTop: 22 }}>Tell WatchLog what each camera is responsible for</h1>
          <p>Classify the site, assign camera purposes, then draw the lines and zones WatchLog should measure on the site PC.</p>
        </div>
      </div>
      {error && <div className="err">{error}</div>}
      {note && <div className="ok-note">{note}</div>}
      {!canManage && <div className={styles.readOnly}>You have read-only access. An Owner or Admin can change camera purposes, schedules and monitoring rules.</div>}

      {!site ? <div className="panel"><div className="empty">No site yet. Add a site before configuring analytics.</div></div> :
      <div className={styles.studioGrid}>
        <aside className={styles.sidebar}>
          <div className={styles.sideSection}>
            <div className={styles.sideTitle}>Site</div>
            <select value={siteId} onChange={(e) => {
              const nextId = e.target.value;
              const next = sites.find((s) => s.id === nextId);
              setSiteId(nextId); setCameraId(next?.cameras?.[0]?.id || ""); setRule(null); setSnapshot(null);
            }}>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <div className={styles.sideTitle}>Site type</div>
            <select value={site.site_type || "custom"} disabled={busy || !canManage}
                    onChange={(e) => setSiteType(e.target.value)}>
              {siteTypes.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
            </select>
          </div>
          <div className={styles.sideSection}>
            <div className={styles.sideTitle}>Cameras</div>
            {cameras.map((c) => <button key={c.id} type="button"
              className={`${styles.cameraBtn} ${c.id === cameraId ? styles.cameraBtnActive : ""}`}
              onClick={() => { setCameraId(c.id); setRule(null); setSnapshot(null); }}>
              <span className={`${styles.dot} ${c.analytics_enabled ? "" : styles.dotMuted}`} />
              <span>{c.name || `Camera ${c.channel}`}</span>
            </button>)}
            {!cameras.length && <div className="muted">Waiting for camera discovery.</div>}
          </div>
        </aside>

        <section className={styles.editor}>
          {camera ? <>
            <div className={styles.card}>
              <div className={styles.cardHead}><div><div className={styles.eyebrow}>Camera context</div><h2>What is this camera responsible for?</h2></div>
                {canManage && <label className={styles.switchLabel}><input type="checkbox" checked={camera.analytics_enabled !== false}
                  onChange={(e) => saveCameraProfile({ enabled: e.target.checked })} /> Analytics enabled</label>}
              </div>
              <div className={styles.context}>
                <div className={styles.field}>
                  <label>Camera</label>
                  <input defaultValue={camera.name || ""} key={camera.id + "-name"} disabled={!canManage}
                    onBlur={(e) => { if (e.target.value !== camera.name) saveCameraProfile({ name: e.target.value }); }} />
                </div>
                <div className={styles.field}>
                  <label>What does this camera watch?</label>
                  <select value={camera.purpose || "custom"} disabled={busy || !canManage}
                    onChange={(e) => saveCameraProfile({ purpose: e.target.value })}>
                    {purposeOptions.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                  </select>
                </div>
              </div>
            </div>

            <div className={styles.card}>
              <div className={styles.cardHead}><div><div className={styles.eyebrow}>Recommended monitoring</div><h2>Useful for this camera</h2></div></div>
              <p className="muted">Recommendations combine the site type with this camera's purpose, so a till camera and perimeter camera do not get the same setup.</p>
              <div className={styles.packRow}>
                {recommended.filter((key) => analyticsByKey[key]).map((key) => <button type="button" className={styles.pack}
                  key={key} disabled={!canManage || !camera.analytics_enabled} onClick={() => startAnalytic(key)}>
                    <strong>{LABELS[key] || analyticsByKey[key]?.label}</strong>
                    <small>{analyticsByKey[key]?.note || "Add monitoring goal"}</small>
                  </button>)}
              </div>
              <div className={styles.alwaysOn}><span className={`${styles.statusDot} ${styles.statusOk}`} /><div><b>Site Health</b><small>Always on. Site Agent, camera silence and recorder faults do not need an Analytics rule.</small></div></div>
            </div>

            <div className={styles.card}>
              <div className={styles.cardHead}><div><div className={styles.eyebrow}>Published</div><h2>Monitoring rules</h2></div><span className={styles.mutedCount}>{rules.length} rule{rules.length === 1 ? "" : "s"}</span></div>
              <div className={styles.ruleList}>
                {rules.length ? rules.map((r) => <div className={styles.rule} key={r.id}>
                  <div><div className={styles.ruleTitle}>{r.name}</div>
                    <div className={styles.ruleMeta}>{LABELS[r.analytic_key] || String(r.analytic_key || r.rule_type).replaceAll("_", " ")} · {(r.object_classes || []).join(", ")} · every {Number(r.sample_seconds || 2)}s</div></div>
                  <div className={styles.ruleActions}>
                    <span className={`${styles.statePill} ${r.enabled ? styles.enabled : styles.paused}`}>{r.enabled ? "Active" : "Paused"}</span>
                    {canManage && <><button className="ghost small" type="button" onClick={() => editRule(r)}>Edit</button>
                    <button className="btn-danger" type="button" onClick={() => deleteRule(r.id)}>Delete</button></>}
                  </div>
                </div>) : <div className={styles.empty}>No monitoring rules for this camera yet.</div>}
              </div>
            </div>

            {rule && canManage && <div className={styles.ruleForm}>
              <h3>{rule.id ? "Edit monitoring rule" : `Add ${rule.name}`}</h3>
              <p className={styles.ruleFormIntro}>Measurements stay separate from incidents unless you explicitly promote this rule.</p>
              <div className={styles.ruleGrid}>
                <div className={styles.field}><label>Name</label><input value={rule.name}
                  onChange={(e) => setRule({ ...rule, name: e.target.value })} /></div>
                <div className={styles.field}><label>Sample every</label><select value={rule.sampleSeconds}
                  onChange={(e) => setRule({ ...rule, sampleSeconds: Number(e.target.value) })}>
                  <option value={1}>1 second</option><option value={2}>2 seconds</option>
                  <option value={5}>5 seconds</option><option value={10}>10 seconds</option>
                </select></div>

                <div className={`${styles.field} ${styles.fieldFull}`}>
                  <label>Objects to measure</label><div className={styles.checks}>
                    {["person","car","motorcycle"].map((cls) => {
                      const allowed = analyticsByKey[rule.analyticKey]?.classes || [];
                      if (!allowed.includes(cls)) return null;
                      return <label className={styles.check} key={cls}>
                        <input type="checkbox" disabled={!canChooseClasses} checked={rule.classes.includes(cls)} onChange={() => toggleClass(cls)} />
                        {cls === "person" ? "People" : cls === "car" ? "Cars" : "Motorcycles"}
                      </label>;
                    })}
                  </div>
                </div>

                {rule.ruleType === "line_crossing" && <>
                  <div className={styles.field}><label>Direction A</label><input value={rule.inLabel}
                    onChange={(e) => setRule({ ...rule, inLabel: e.target.value })} /></div>
                  <div className={styles.field}><label>Direction B</label><input value={rule.outLabel}
                    onChange={(e) => setRule({ ...rule, outLabel: e.target.value })} /></div>
                </>}
                {rule.ruleType === "zone_dwell" && <div className={styles.field}><label>Minimum dwell</label>
                  <input type="number" min="1" max="86400" value={rule.dwellSeconds}
                    onChange={(e) => setRule({ ...rule, dwellSeconds: e.target.value })} /></div>}

                <div className={styles.field}><label>Schedule</label>
                  <select value={rule.scheduleId} onChange={(e) => setRule({ ...rule, scheduleId: e.target.value })}>
                    <option value="">{rule.ruleType === "schedule_activity" ? "Choose business hours" : "Always"}</option>
                    {schedules.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select></div>

                {rule.ruleType === "schedule_activity" && !schedules.length && <div className={`${styles.card} ${styles.fieldFull}`}>
                  <h3>Create business hours</h3><div className={styles.context}>
                    <div className={styles.field}><label>Starts</label><input type="time" value={businessStart} onChange={(e) => setBusinessStart(e.target.value)} /></div>
                    <div className={styles.field}><label>Ends</label><input type="time" value={businessEnd} onChange={(e) => setBusinessEnd(e.target.value)} /></div>
                  </div><button type="button" disabled={busy} onClick={createBusinessSchedule}>Create Monday to Friday schedule</button>
                </div>}

                <div className={`${styles.field} ${styles.fieldFull}`}>
                  <label>Incident behavior</label><div className={styles.checks}>
                    <label className={styles.check}><input type="checkbox" checked={rule.promoteIncident}
                      onChange={(e) => setRule({ ...rule, promoteIncident: e.target.checked,
                        severity: e.target.checked ? "incident" : "measurement" })} />
                      Also create an incident for human review
                    </label>
                  </div>
                </div>
              </div>

              {needsScene && <div className={styles.geometryBlock}>
                <div className={styles.geometryTitle}><div><b>Monitoring geometry</b><small>{rule.ruleType === "line_crossing" ? "Draw the line a person or vehicle must cross." : "Draw the area WatchLog should measure."}</small></div></div>
                <div className={styles.scene}>
                  {snapshot ? <img src={snapshot} alt={`Configuration still from ${camera.name || "camera"}`} /> :
                    <div className={styles.sceneEmpty}><div><b>No configuration still yet.</b><br />Request one current still from the Site Agent, then draw on the actual scene.</div></div>}
                  <svg ref={svgRef} viewBox="0 0 100 100" preserveAspectRatio="none" onPointerDown={sceneClick}
                       aria-label="Monitoring geometry editor">
                    {rule.ruleType === "line_crossing" && rule.points.length === 2 &&
                      <line x1={rule.points[0][0]*100} y1={rule.points[0][1]*100}
                            x2={rule.points[1][0]*100} y2={rule.points[1][1]*100}
                            stroke="#72D4FF" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />}
                    {rule.ruleType !== "line_crossing" && rule.points.length === 2 &&
                      <polyline points={pointString(rule.points)} fill="none" stroke="#72D4FF" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />}
                    {rule.ruleType !== "line_crossing" && rule.points.length >= 3 &&
                      <polygon points={pointString(rule.points)} fill="rgba(23,72,211,.18)" stroke="#72D4FF"
                               strokeWidth="1.2" vectorEffect="non-scaling-stroke" />}
                    {rule.points.map((p, i) => <circle key={i} cx={p[0]*100} cy={p[1]*100} r="1.2"
                                                       fill="#72D4FF" stroke="#07111F" strokeWidth=".4" />)}
                  </svg>
                  <div className={styles.sceneHelp}>{rule.ruleType === "line_crossing" ? "Click two points across the path." : "Click 3 to 8 points around the area."}</div>
                </div>
                <div className={styles.geometryActions}>
                  <span className={styles.geometryStatus}>{rule.points.length} point{rule.points.length === 1 ? "" : "s"} · coordinates stay normalized to the camera frame</span>
                  <div className={styles.actions}>
                    {!snapshot && <button type="button" className="ghost small" disabled={busy} onClick={requestSnapshot}>
                      {busy ? "Waiting for still..." : camera.snapshot_requested ? "Check requested still" : "Request camera still"}</button>}
                    <button type="button" className="ghost small" onClick={() => setRule({ ...rule, points: [] })}>Clear geometry</button>
                  </div>
                </div>
              </div>}

              <div className={styles.actions} style={{ marginTop: 22 }}>
                <button type="button" disabled={busy} onClick={saveRule}>{busy ? "Saving..." : "Publish monitoring rule"}</button>
                <button type="button" className="ghost" onClick={() => setRule(null)}>Cancel</button>
              </div>
            </div>}
          </> : <div className={styles.card}><div className={styles.empty}>Choose a discovered camera to configure monitoring.</div></div>}
        </section>
      </div>}
    </main>
  </div>;
}
