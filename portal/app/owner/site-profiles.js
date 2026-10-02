// WatchLog site-type intelligence profiles (Phase 28).
//
// Tenant -> Site -> site_type -> profile. The profile decides WHICH owner questions, modules, comparisons
// and Ask WatchLog questions a site gets; the governed data decides WHETHER each module can render.
// Semantic policy (owner questions, metric status, prohibitions) lives in ai-harness/site-types/*.yaml;
// this file is the portal's deterministic composition of that policy. It never invents a figure:
//   site relevance + explicitly configured camera purpose + governed evidence (+ verified coverage for
//   "no activity" claims) -> module eligibility. Anything missing is not rendered (or shown as Unknown
//   where the absence itself matters). Camera purpose is explicit configuration; names are never read.

export const BUSINESS_TYPES = ["office", "warehouse", "factory", "retail", "restaurant"];

// Analytics-studio site vocabulary (sites.site_type) -> business profile. The business context
// (site_business_context.site_type) is authoritative; this is only the fallback when it is unset.
const STUDIO_ALIAS = { office_commercial: "office", warehouse_logistics: "warehouse", manufacturing: "factory", retail: "retail", restaurant: "restaurant" };

export function resolveSiteType(contextType, studioType) {
  const c = String(contextType || "").toLowerCase();
  if (BUSINESS_TYPES.includes(c)) return c;
  const s = STUDIO_ALIAS[String(studioType || "").toLowerCase()];
  return s || "general";
}

// Camera purposes offered in Setup. Shared purposes first, then the site type's own areas.
// Earlier values (management, loading, queue) stay valid so existing configuration keeps its meaning.
const SHARED_PURPOSES = [["entrance", "Entrance / exit"], ["restricted", "Restricted area"], ["parking", "Parking / vehicle access"], ["perimeter", "Perimeter"], ["general", "General area"]];
const TYPE_PURPOSES = {
  office: [["reception", "Reception / lobby"], ["workspace", "Workspace / office floor"], ["meeting_area", "Meeting room"], ["common_area", "Pantry / common area"], ["corridor", "Corridor"], ["management", "Office / management"]],
  warehouse: [["gate", "Gate"], ["yard", "Yard"], ["receiving", "Receiving"], ["loading_dock", "Loading dock"], ["dispatch", "Dispatch"], ["storage", "Storage"], ["restricted_storage", "Restricted storage"]],
  factory: [["gate", "Gate"], ["production_zone", "Production zone"], ["assembly", "Assembly"], ["packing", "Packing"], ["raw_materials", "Raw materials"], ["finished_goods", "Finished goods"], ["dispatch", "Dispatch"], ["maintenance", "Maintenance"]],
  retail: [["sales_floor", "Sales floor"], ["product_zone", "Product zone"], ["promotional_zone", "Promotional zone"], ["checkout", "Checkout"], ["queue_area", "Queue area"], ["stock_room", "Stock room"]],
  restaurant: [["dining", "Dining area"], ["outdoor_seating", "Outdoor seating"], ["cashier", "Cashier"], ["kitchen", "Kitchen"], ["service_area", "Service area"]],
  general: [["reception", "Reception / lobby"], ["management", "Office / management"], ["loading", "Loading / service"], ["queue", "Queue / service area"]],
};
export function purposeOptions(siteType) {
  return [...SHARED_PURPOSES, ...(TYPE_PURPOSES[siteType] || TYPE_PURPOSES.general)];
}

// Zone = a business area the owner reasons about, built from explicit camera purposes.
const ZONES = {
  office: [
    { key: "entrance", label: "Entrance", purposes: ["entrance"] },
    { key: "reception", label: "Reception", purposes: ["reception"] },
    { key: "workspace", label: "Workspace", purposes: ["workspace"] },
    { key: "meeting_area", label: "Meeting rooms", purposes: ["meeting_area"] },
    { key: "common_area", label: "Common areas", purposes: ["common_area", "corridor"] },
    { key: "management", label: "Management offices", purposes: ["management"] },
    { key: "restricted", label: "Restricted areas", purposes: ["restricted"], security: true },
  ],
  warehouse: [
    { key: "gate", label: "Gate", purposes: ["gate", "entrance"] },
    { key: "yard", label: "Yard", purposes: ["yard", "parking"] },
    { key: "receiving", label: "Receiving", purposes: ["receiving"], operational: true },
    { key: "loading_dock", label: "Loading docks", purposes: ["loading_dock", "loading"], operational: true },
    { key: "dispatch", label: "Dispatch", purposes: ["dispatch"], operational: true },
    { key: "storage", label: "Storage", purposes: ["storage"] },
    { key: "restricted_storage", label: "Restricted storage", purposes: ["restricted_storage", "restricted"], security: true },
  ],
  factory: [
    { key: "gate", label: "Gate", purposes: ["gate", "entrance"] },
    { key: "production_zone", label: "Production zones", purposes: ["production_zone"], operational: true },
    { key: "assembly", label: "Assembly", purposes: ["assembly"], operational: true },
    { key: "packing", label: "Packing", purposes: ["packing"], operational: true },
    { key: "raw_materials", label: "Raw materials", purposes: ["raw_materials"], logistics: true },
    { key: "finished_goods", label: "Finished goods", purposes: ["finished_goods"], logistics: true },
    { key: "dispatch", label: "Dispatch", purposes: ["dispatch", "loading"], logistics: true },
    { key: "maintenance", label: "Maintenance", purposes: ["maintenance"] },
    { key: "restricted", label: "Restricted areas", purposes: ["restricted"], security: true },
  ],
  retail: [
    { key: "entrance", label: "Entrance", purposes: ["entrance"] },
    { key: "sales_floor", label: "Sales floor", purposes: ["sales_floor"] },
    { key: "product_zone", label: "Product zones", purposes: ["product_zone"] },
    { key: "promotional_zone", label: "Promotional zones", purposes: ["promotional_zone"] },
    { key: "checkout", label: "Checkout", purposes: ["checkout", "checkout_till", "queue_area", "queue"], operational: true },
    { key: "stock_room", label: "Stock room", purposes: ["stock_room"] },
    { key: "restricted", label: "Restricted areas", purposes: ["restricted"], security: true },
  ],
};

const PROFILES = {
  office: {
    label: "Office", dayNoun: "working day", composer: "business",
    period: { rpc: "wl_office_period", schema: "office-period-v1", measure: "activity_detections", measureNote: "camera detections, not unique people" },
    operationsTitle: "Office activity",
    activityNoun: "workplace activity",
    gapZones: [],
    vehicleZones: ["parking"],
    comparisonLabel: "Observed workplace activity",
    home: ["attention", "timeline", "zones", "change", "afterHours"],
    ask: [
      { q: "When was activity first observed today?", needs: "opening" },
      { q: "Was anyone here after closing?", needs: "afterHours" },
      { q: "Which area was busiest?", needs: "zones" },
      { q: "What changed from the previous working days?", needs: "comparison" },
    ],
    prohibited: ["attendance", "employees present", "headcount", "productivity"],
  },
  warehouse: {
    label: "Warehouse", dayNoun: "working day", composer: "business",
    period: { rpc: "wl_site_period", schema: "site-period-v1", measure: "activity_episodes", measureNote: "activity episodes, not unique people" },
    // Notable quiet period: no observed activity for this long inside configured hours. NOT field-validated;
    // crossing it is an observation, never evidence of downtime, delay or lost productivity.
    quietPeriod: { minutes: 45, validated: false },
    operationsTitle: "Loading and dispatch",
    activityNoun: "loading and dispatch activity",
    gapZones: ["loading_dock", "dispatch", "receiving"],
    vehicleZones: ["gate", "yard", "loading_dock", "dispatch"],
    comparisonLabel: "Observed people activity",
    home: ["attention", "timeline", "zones", "gaps", "vehicles", "change"],
    ask: [
      { q: "Which loading area was least active?", needs: "zones" },
      { q: "Were there notable quiet periods at the docks?", needs: "gaps" },
      { q: "When was dispatch busiest?", needs: "zones" },
      { q: "Was there activity after hours?", needs: "afterHours" },
    ],
    prohibited: ["shipments", "orders", "tonnes", "throughput", "dispatch completed"],
  },
  factory: {
    label: "Factory", dayNoun: "shift day", composer: "business",
    period: { rpc: "wl_site_period", schema: "site-period-v1", measure: "activity_episodes", measureNote: "activity episodes, not unique people" },
    // Notable quiet period: no observed activity for this long inside configured hours. NOT field-validated;
    // crossing it is an observation, never evidence of downtime, delay or lost productivity.
    quietPeriod: { minutes: 45, validated: false },
    operationsTitle: "Production-area activity",
    activityNoun: "production-area activity",
    gapZones: ["production_zone", "assembly", "packing"],
    vehicleZones: ["gate", "dispatch", "finished_goods", "raw_materials"],
    comparisonLabel: "Observed people activity",
    home: ["attention", "timeline", "zones", "gaps", "logistics", "change"],
    ask: [
      { q: "Were there notable quiet periods in production areas?", needs: "gaps" },
      { q: "Which production area was active longest?", needs: "zones" },
      { q: "When was shift activity first observed?", needs: "opening" },
      { q: "Was there after-hours production-area activity?", needs: "afterHours" },
    ],
    prohibited: ["production output", "units produced", "production fell", "efficiency", "oee"],
  },
  retail: {
    label: "Retail", dayNoun: "trading day", composer: "business",
    period: { rpc: "wl_site_period", schema: "site-period-v1", measure: "activity_episodes", measureNote: "activity episodes, not unique people" },
    operationsTitle: "Store activity",
    activityNoun: "store activity",
    gapZones: [],
    vehicleZones: ["parking"],
    comparisonLabel: "Observed people activity",
    home: ["attention", "timeline", "zones", "checkout", "change"],
    ask: [
      { q: "What was the busiest hour?", needs: "timeline" },
      { q: "Which store area had the most activity?", needs: "zones" },
      { q: "When was checkout busiest?", needs: "checkout" },
      { q: "How did entrance activity compare with the previous trading days?", needs: "comparison" },
    ],
    prohibited: ["sales", "revenue", "conversion", "basket", "purchases", "customers served"],
  },
  restaurant: {
    label: "Restaurant", dayNoun: "service day", composer: "restaurant",
    // Restaurant intelligence (tables, covers, observed service timing) needs a calibrated restaurant
    // configuration; its period comes from its own governed restaurant period, not wl_site_period.
    requires: "restaurant_config",
    period: { rpc: "wl_restaurant_period", schema: "restaurant-period-v2" },
    ask: [
      { q: "When was the rush on the latest service day?" },
      { q: "Which floor was busiest?" },
      { q: "Did service slow during the peak?" },
      { q: "What changed from the previous service days?" },
    ],
    prohibited: ["revenue", "sales", "unique customers", "order-to-serve", "pos"],
  },
  general: {
    label: "Site", dayNoun: "day", composer: "general", period: null,
    operationsTitle: "Site activity",
    activityNoun: "site activity",
    gapZones: [],
    vehicleZones: [],
    comparisonLabel: "Observed activity",
    home: ["attention", "timeline", "change"],
    ask: [
      { q: "What needs my attention right now?" },
      { q: "Was the latest reporting period fully monitored?" },
      { q: "What happened during the latest completed business day?" },
    ],
    prohibited: [],
  },
};

export function siteProfile(siteType) {
  const key = PROFILES[siteType] ? siteType : "general";
  return { key, ...PROFILES[key], zones: ZONES[key] || [] };
}

// The ONE registry entry point every page uses (restaurant included). The profile decides the composer:
//   restaurant -> calibrated restaurant modules (needs an enabled restaurant configuration)
//   business   -> office / warehouse / factory / retail site-type modules
//   general    -> generic attention, coverage and activity-rule view
export function selectSiteProfile({ restaurantConfig, contextType, studioType } = {}) {
  const calibrated = !!(restaurantConfig && restaurantConfig.enabled === true);
  const profile = siteProfile(calibrated ? "restaurant" : resolveSiteType(contextType, studioType));
  const composer = profile.requires === "restaurant_config" && !calibrated ? "general" : profile.composer;
  return { ...profile, composer };
}

// A governed period is only read by the profile whose schema it carries: warehouse, factory and retail
// can never be handed office-period-v1 facts (and offices never read site-period-v1 as office facts).
export function acceptPeriod(profile, period) {
  return !!(period && period.enabled === true && profile && profile.period && period.schema === profile.period.schema);
}
export function periodMeasure(profile) {
  const p = (profile && profile.period) || {};
  const key = p.measure || "activity_detections";
  return { key, delta: key + "_delta", label: (profile && profile.comparisonLabel) || "Observed activity", note: p.measureNote || "not unique people" };
}
// Site-period facts by profile area: configured purposes summed into the profile's zones, current vs
// previous. Unassigned or unmapped purposes are not attributed to any area.
export function periodAreas(profile, period) {
  if (!acceptPeriod(profile, period) || !Array.isArray(period.summary?.by_purpose)) return [];
  const sum = rows => { const out = {}; for (const r of rows || []) { const z = zoneFor(profile, r.purpose); if (z) out[z] = (out[z] || 0) + Number(r.episodes || 0) + Number(r.vehicle_episodes || 0); } return out; };
  const cur = sum(period.summary.by_purpose), prev = sum(period.previous_period?.by_purpose);
  return (profile.zones || []).filter(z => z.key in cur || z.key in prev).map(z => ({ key: z.key, label: z.label, current: cur[z.key] || 0, previous: prev[z.key] || 0, delta: (cur[z.key] || 0) - (prev[z.key] || 0), operational: !!z.operational }));
}

// ---------------------------------------------------------------------------------------------
// Deterministic derivation from ONE governed business-day dataset (wl_my_daily_intelligence).
// ---------------------------------------------------------------------------------------------
function mins(hhmm) {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || ""));
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
export function hhmm(total) {
  if (total === null || total === undefined) return "";
  const t = ((Math.round(total) % 1440) + 1440) % 1440;
  return String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0");
}
export function minutesLabel(n) {
  const v = Math.round(Number(n) || 0);
  if (v < 60) return v + " min";
  return Math.floor(v / 60) + "h " + String(v % 60).padStart(2, "0") + "m";
}
// Site-local minutes for an ISO timestamp (coverage gaps arrive as instants). Unparseable -> null.
function localMinutes(value, timeZone) {
  const plain = mins(value);
  if (plain !== null && !/T|Z|[+-]\d{2}:?\d{2}$/.test(String(value))) return plain;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-GB", { timeZone: timeZone || "Asia/Karachi", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(d);
    const h = Number(parts.find(x => x.type === "hour")?.value), m = Number(parts.find(x => x.type === "minute")?.value);
    return Number.isFinite(h) && Number.isFinite(m) ? (h % 24) * 60 + m : null;
  } catch { return null; }
}
function mergeIntervals(list) {
  const s = list.filter(x => x[0] !== null && x[1] !== null).map(x => [x[0], Math.max(x[0], x[1])]).sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const iv of s) {
    if (out.length && iv[0] <= out[out.length - 1][1]) out[out.length - 1][1] = Math.max(out[out.length - 1][1], iv[1]);
    else out.push([...iv]);
  }
  return out;
}
function median(xs) {
  const a = xs.filter(Number.isFinite).sort((p, q) => p - q);
  if (!a.length) return null;
  const m = Math.floor(a.length / 2);
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

// purpose -> zone key for this profile (explicit purpose only; unset purpose stays unmapped)
export function zoneFor(profile, purpose) {
  const p = String(purpose || "").trim().toLowerCase();
  if (!p) return null;
  const z = (profile.zones || []).find(z => z.purposes.includes(p));
  return z ? z.key : null;
}

/**
 * derive the business-day picture for a profile.
 * daily: wl_my_daily_intelligence payload; cameras: configured cameras [{name, purpose, monitor}];
 * hours: configured {open_time, close_time} from the business context; coverage: coverageTruth().
 */
export function deriveSiteDay({ profile, daily, cameras, hours, coverage, gapThresholdMinutes }) {
  const quietMinutes = gapThresholdMinutes ?? profile?.quietPeriod?.minutes ?? 45;
  const out = { quietMinutes: null, zones: [], timeline: [], vehicles: null, gaps: [], gapsBlocked: null, opening: null, afterHours: null, firstActivity: null, lastActivity: null, peakHour: null, configuredZones: [] };
  if (!daily) return out;
  out.quietMinutes = (profile?.gapZones || []).length ? quietMinutes : null;
  const purposeByCamera = new Map((cameras || []).filter(c => c && c.name).map(c => [String(c.name), c.purpose || ""]));
  const configured = new Set((cameras || []).filter(c => c && c.monitor !== false).map(c => zoneFor(profile, c.purpose)).filter(Boolean));
  out.configuredZones = (profile.zones || []).filter(z => configured.has(z.key)).map(z => z.key);
  const episodes = (daily.access_windows || []).map(e => {
    const purpose = e.purpose || purposeByCamera.get(String(e.camera)) || "";
    return { ...e, zone: zoneFor(profile, purpose), s: mins(e.start), e: mins(e.end) };
  });

  // Zones: governed episodes grouped by the explicitly configured purpose of each camera.
  for (const z of profile.zones || []) {
    if (!configured.has(z.key)) continue;
    const eps = episodes.filter(e => e.zone === z.key);
    const merged = mergeIntervals(eps.map(e => [e.s, e.e ?? e.s]));
    const active = merged.reduce((n, iv) => n + Math.max(1, iv[1] - iv[0]), 0);
    out.zones.push({
      key: z.key, label: z.label, operational: !!z.operational, logistics: !!z.logistics, security: !!z.security,
      episodes: eps.length, people: eps.filter(e => e.object_class !== "vehicle").length, vehicles: eps.filter(e => e.object_class === "vehicle").length,
      activeMinutes: eps.length ? active : 0, first: eps.length ? hhmm(Math.min(...eps.map(e => e.s))) : null, last: eps.length ? hhmm(Math.max(...eps.map(e => e.e ?? e.s))) : null,
      merged,
    });
  }

  // Hourly timeline (episode starts per hour) - only hours that carry governed episodes or sit between them.
  const starts = episodes.map(e => e.s).filter(x => x !== null);
  if (starts.length) {
    const h0 = Math.floor(Math.min(...starts) / 60), h1 = Math.floor(Math.max(...starts) / 60);
    for (let h = h0; h <= h1; h++) {
      const eps = episodes.filter(e => e.s !== null && Math.floor(e.s / 60) === h);
      // Share of the hour that configured operational areas were active, averaged across those areas (0-100).
      const opZones = out.zones.filter(z => z.operational);
      const share = opZones.length ? Math.round(opZones.reduce((n, z) => n + z.merged.reduce((m, iv) => m + Math.max(0, Math.min(iv[1], h * 60 + 60) - Math.max(iv[0], h * 60)), 0), 0) / (opZones.length * 60) * 100) : null;
      out.timeline.push({ hour: h, episodes: eps.length, operational: eps.filter(e => (profile.zones || []).some(z => z.key === e.zone && (z.operational || z.logistics))).length, operationalShare: share });
    }
    const peak = out.timeline.reduce((b, x) => (x.episodes > (b ? b.episodes : -1) ? x : b), null);
    out.peakHour = peak ? peak.hour : null;
  }

  // First/last observed activity from the site-neutral governed episodes. Only the office profile may fall
  // back to the office brief (its own semantics) when no episodes were returned.
  const ends = episodes.map(e => e.e ?? e.s).filter(x => x !== null);
  const officeBrief = profile?.key === "office" ? (daily.office || {}) : {};
  out.firstActivity = starts.length ? hhmm(Math.min(...starts)) : (officeBrief.coverage?.first || null);
  out.lastActivity = ends.length ? hhmm(Math.max(...ends)) : (officeBrief.coverage?.last || null);
  const b = daily.day_boundaries || {};
  out.opening = b.opening_at ? { at: b.opening_at, confidence: b.confidence, low: !!b.low_confidence, configured: hours?.open_time ? String(hours.open_time).slice(0, 5) : null } : null;
  // "Activity began at X" is only a conclusion when the time before X was verified. Otherwise X is just the
  // first OBSERVED activity: full day coverage, or partial coverage whose known gaps all start after X.
  if (out.opening) {
    const at = mins(out.opening.at);
    const known = Array.isArray(daily.coverage?.gaps) ? daily.coverage.gaps : null;
    const before = known && known.some(g => { const gs = localMinutes(g.start_local || g.start, daily.meta?.timezone); return gs === null || (at !== null && gs < at); });
    out.opening.verified = !out.opening.low && at !== null && (coverage?.fullyVerified === true || (coverage?.partial === true && known !== null && known.length > 0 && !before));
  }
  const ah = daily.after_hours || b.after_hours;
  out.afterHours = ah && ah.verified ? { count: Number(ah.count || 0) } : null;

  // Vehicle episodes at configured vehicle-relevant cameras. Episodes at a camera, NOT separate vehicles
  // and NOT site time (that needs cross-camera journey correlation).
  const vZones = new Set(profile.vehicleZones || []);
  const vEps = episodes.filter(e => e.object_class === "vehicle" && vZones.has(e.zone));
  const anyVehicleCamera = (profile.zones || []).some(z => vZones.has(z.key) && configured.has(z.key));
  if (anyVehicleCamera && vEps.length) {
    const dwell = vEps.map(e => Number(e.dwell_seconds)).filter(Number.isFinite);
    out.vehicles = { episodes: vEps.length, medianDwellMinutes: dwell.length ? median(dwell) / 60 : null, longestDwellMinutes: dwell.length ? Math.max(...dwell) / 60 : null, longest: vEps.slice().sort((p, q) => Number(q.dwell_seconds || 0) - Number(p.dwell_seconds || 0))[0] || null };
  }

  // Inactive periods inside configured operating hours, per operational zone. A "no activity" claim is
  // only made when the window is verified: full coverage, or known coverage gaps that do not overlap it.
  const open = mins(hours?.open_time), configuredClose = mins(hours?.close_time);
  // A day still in progress is only scanned up to the time the data was produced, never to closing.
  const until = daily.meta?.partial_day ? localMinutes(daily.meta?.generated_at, daily.meta?.timezone) : null;
  const close = configuredClose !== null && until !== null ? Math.min(configuredClose, until) : configuredClose;
  const gapZones = new Set(profile.gapZones || []);
  if (open !== null && close !== null && close > open && gapZones.size) {
    const known = Array.isArray(daily.coverage?.gaps) ? daily.coverage.gaps : null;
    const canClaim = coverage?.fullyVerified || (coverage?.partial && known && known.length);
    for (const z of out.zones.filter(z => gapZones.has(z.key) && z.episodes > 0)) {
      let cursor = open, best = null;
      for (const iv of [...z.merged, [close, close]]) {
        const s = Math.max(open, Math.min(close, iv[0]));
        if (s - cursor >= quietMinutes && (!best || s - cursor > best.minutes)) best = { start: cursor, end: s, minutes: s - cursor };
        cursor = Math.max(cursor, Math.min(close, iv[1]));
      }
      if (!best) continue;
      if (!canClaim) { out.gapsBlocked = out.gapsBlocked || { reason: coverage?.known ? "partial" : "unknown" }; continue; }
      if (known && known.length) {
        const tz = daily.meta?.timezone;
        // Conservative: a coverage gap whose times cannot be placed blocks the claim.
        const overlaps = known.some(g => { const gs = localMinutes(g.start_local || g.start, tz), ge = localMinutes(g.end_local || g.end, tz); return gs === null || ge === null || (gs < best.end && ge > best.start); });
        if (overlaps) { out.gapsBlocked = { reason: "overlap" }; continue; }
      }
      out.gaps.push({ zone: z.key, label: z.label, start: hhmm(best.start), end: hhmm(best.end), minutes: best.minutes });
    }
    out.gaps.sort((a, b2) => b2.minutes - a.minutes);
  }
  for (const z of out.zones) delete z.merged;
  return out;
}

// Which modules can render. Relevance comes from the profile; eligibility from governed data.
export function eligible(profile, day, comparison) {
  const zonesWithActivity = day.zones.filter(z => z.episodes > 0);
  const checkout = day.zones.find(z => z.key === "checkout");
  return {
    timeline: day.timeline.length > 0,
    zones: zonesWithActivity.length > 0,
    gaps: (profile.gapZones || []).length > 0 && (day.gaps.length > 0 || !!day.gapsBlocked),
    vehicles: !!day.vehicles,
    logistics: day.zones.some(z => z.logistics && z.episodes > 0),
    checkout: profile.key === "retail" && !!checkout,
    afterHours: !!day.afterHours,
    opening: !!day.opening || !!day.firstActivity,
    comparison: acceptPeriod(profile, comparison),
  };
}

// What a site's configuration can answer before any day is derived (Ask starters, setup guidance).
export function configuredCapabilities(profile, ctx) {
  const cams = (ctx?.cameras || []).filter(c => c && c.monitor !== false);
  const zones = new Set(cams.map(c => zoneFor(profile, c.purpose)).filter(Boolean));
  const bc = ctx?.business_context || {};
  const hours = mins(bc.open_time) !== null && mins(bc.close_time) !== null;
  return {
    timeline: cams.length > 0,
    zones: zones.size > 0,
    gaps: hours && (profile.gapZones || []).some(z => zones.has(z)),
    vehicles: (profile.vehicleZones || []).some(z => zones.has(z)),
    checkout: profile.key === "retail" && zones.has("checkout"),
    afterHours: hours,
    opening: hours,
    // The working-day comparison is governed for offices today; other types need migration 0143.
    comparison: profile.key === "office",
  };
}

export function askQuestions(profile, can) {
  return profile.ask.filter(x => !x.needs || (can && can[x.needs])).map(x => x.q);
}
