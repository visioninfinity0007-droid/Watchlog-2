// Phase 28 site-type intelligence: deterministic unit checks for portal/app/owner/site-profiles.js.
// Run through test_site_type_intelligence_contract.py, which copies the module to an .mjs first.
import assert from "node:assert/strict";
const P = await import(process.argv[2]);
const { siteProfile, resolveSiteType, purposeOptions, deriveSiteDay, eligible, askQuestions, configuredCapabilities, zoneFor, selectSiteProfile, acceptPeriod, periodMeasure, periodAreas } = P;

const cam = (name, purpose, monitor = true) => ({ name, purpose, monitor });
const ep = (camera, start, end, extra = {}) => ({ camera, start, end, ...extra });
const VERIFIED = { known: true, fullyVerified: true, partial: false };
const PARTIAL = { known: true, fullyVerified: false, partial: true };
const UNKNOWN = { known: false, fullyVerified: false, partial: false };
const HOURS = { open_time: "09:00:00", close_time: "18:00:00" };
let n = 0;
const t = (name, fn) => { fn(); n++; };

// --- Resolution ------------------------------------------------------------------------------
t("site types resolve from context then studio aliases; anything else is general", () => {
  for (const k of ["office", "warehouse", "factory", "retail", "restaurant"]) assert.equal(resolveSiteType(k), k);
  assert.equal(resolveSiteType(null, "warehouse_logistics"), "warehouse");
  assert.equal(resolveSiteType(null, "manufacturing"), "factory");
  assert.equal(resolveSiteType(null, "office_commercial"), "office");
  assert.equal(resolveSiteType("", ""), "general");
  assert.equal(resolveSiteType("hospital"), "general");
  assert.equal(siteProfile("unknown-type").key, "general");
});

// --- Camera purposes per type (setup) -------------------------------------------------------
t("setup offers type-specific purposes only", () => {
  const keys = type => purposeOptions(type).map(([k]) => k);
  assert.ok(keys("warehouse").includes("loading_dock") && !keys("warehouse").includes("checkout"));
  assert.ok(keys("retail").includes("checkout") && !keys("retail").includes("loading_dock"));
  assert.ok(keys("factory").includes("production_zone") && !keys("factory").includes("dining"));
  assert.ok(keys("restaurant").includes("dining") && !keys("restaurant").includes("loading_dock"));
  assert.ok(keys("office").includes("meeting_area") && !keys("office").includes("dining") && !keys("office").includes("loading_dock"));
  for (const type of ["office", "warehouse", "factory", "retail", "restaurant", "general"])
    for (const shared of ["entrance", "restricted", "parking", "perimeter", "general"]) assert.ok(keys(type).includes(shared), type + " " + shared);
});

// --- Negative tests: no cross-vertical modules -----------------------------------------------
t("office has no table, dock, checkout or production zones", () => {
  const z = siteProfile("office").zones.map(x => x.key).join(" ");
  assert.doesNotMatch(z, /table|dining|loading|dock|checkout|production/);
});
t("restaurant has no loading docks; warehouse no checkout; factory no output; retail no food timing", () => {
  assert.ok(!(siteProfile("restaurant").zones || []).some(z => /dock|loading/.test(z.key)));
  assert.ok(!siteProfile("warehouse").zones.some(z => z.key === "checkout"));
  assert.ok(siteProfile("factory").prohibited.includes("production output"));
  assert.ok(!siteProfile("factory").ask.some(a => /output|units|efficien/i.test(a.q)));
  assert.ok(!siteProfile("retail").ask.some(a => /food|serve|table|kitchen/i.test(a.q)));
  assert.ok(siteProfile("retail").prohibited.includes("sales"));
  assert.ok(siteProfile("office").prohibited.includes("attendance"));
});
t("an unset or general camera purpose never maps to an operational zone", () => {
  for (const type of ["office", "warehouse", "factory", "retail"]) {
    const p = siteProfile(type);
    assert.equal(zoneFor(p, ""), null);
    assert.equal(zoneFor(p, null), null);
    const g = zoneFor(p, "general");
    assert.ok(!g || !p.zones.find(z => z.key === g).operational, type);
  }
});

// --- Warehouse derivation --------------------------------------------------------------------
const whCams = [cam("Dock 1", "loading_dock"), cam("Dock 2", "loading_dock"), cam("Gate", "gate"), cam("Store", "storage")];
const whDaily = {
  access_windows: [
    ep("Dock 1", "09:05", "10:30"), ep("Dock 1", "15:40", "17:30"),
    ep("Dock 2", "09:30", "10:00"),
    ep("Gate", "09:00", "09:20", { object_class: "vehicle", dwell_seconds: 1200 }),
    ep("Gate", "13:00", "13:05", { object_class: "vehicle", dwell_seconds: 300 }),
  ],
  coverage: { gaps: [] },
  meta: { timezone: "Asia/Karachi" },
};
t("warehouse: verified coverage allows a dock inactivity claim inside configured hours", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: whDaily, cameras: whCams, hours: HOURS, coverage: VERIFIED });
  const dock = d.zones.find(z => z.key === "loading_dock");
  assert.equal(dock.episodes, 3);
  assert.ok(d.gaps.length >= 1);
  assert.equal(d.gaps[0].zone, "loading_dock");
  assert.deepEqual([d.gaps[0].start, d.gaps[0].end, d.gaps[0].minutes], ["10:30", "15:40", 310]);
  assert.equal(d.vehicles.episodes, 2);
  assert.equal(d.vehicles.longestDwellMinutes, 20);
  const can = eligible(p, d, null);
  assert.equal(can.checkout, false, "warehouse never renders checkout");
  assert.equal(can.comparison, false, "no comparison without an enabled governed period");
});
t("warehouse: merged dock intervals leave no gap when the two docks overlap", () => {
  const p = siteProfile("warehouse");
  const daily = { ...whDaily, access_windows: [...whDaily.access_windows, ep("Dock 2", "10:00", "17:00")] };
  const d = deriveSiteDay({ profile: p, daily, cameras: whCams, hours: HOURS, coverage: VERIFIED });
  // Dock 2 covers 10:00-17:00, so the zone's merged activity has no 45-minute hole inside 09:00-18:00.
  assert.equal(d.gaps.length, 0);
});
t("partial coverage without known gap windows blocks the inactivity claim", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: { ...whDaily, coverage: {} }, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: PARTIAL });
  assert.equal(d.gaps.length, 0);
  assert.equal(d.gapsBlocked.reason, "partial");
});
t("unknown coverage blocks the inactivity claim", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: whDaily, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: UNKNOWN });
  assert.equal(d.gaps.length, 0);
  assert.equal(d.gapsBlocked.reason, "unknown");
});
t("a coverage gap overlapping the quiet period blocks the claim; an unparseable one does too", () => {
  const p = siteProfile("warehouse");
  const base = { ...whDaily, access_windows: whDaily.access_windows.filter(e => e.camera === "Dock 1") };
  const over = deriveSiteDay({ profile: p, daily: { ...base, coverage: { gaps: [{ start: "11:00", end: "12:00" }] } }, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: PARTIAL });
  assert.equal(over.gaps.length, 0); assert.equal(over.gapsBlocked.reason, "overlap");
  const bad = deriveSiteDay({ profile: p, daily: { ...base, coverage: { gaps: [{ start: "not-a-time", end: "??" }] } }, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: PARTIAL });
  assert.equal(bad.gaps.length, 0);
  const clear = deriveSiteDay({ profile: p, daily: { ...base, coverage: { gaps: [{ start: "19:00", end: "20:00" }] } }, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: PARTIAL });
  assert.ok(clear.gaps.length === 1 && clear.gaps[0].start === "10:30" && clear.gaps[0].end === "15:40");
});
t("an in-progress day is only scanned up to when the data was produced", () => {
  const p = siteProfile("warehouse");
  const daily = { access_windows: [ep("Dock 1", "09:00", "10:00")], coverage: { gaps: [] }, meta: { partial_day: true, generated_at: "10:20", timezone: "Asia/Karachi" } };
  const d = deriveSiteDay({ profile: p, daily, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: VERIFIED });
  assert.equal(d.gaps.length, 0, "20 minutes since the last episode is not a 45-minute gap");
});
t("no configured hours means no inactivity claims", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: whDaily, cameras: whCams, hours: {}, coverage: VERIFIED });
  assert.equal(d.gaps.length, 0); assert.equal(d.gapsBlocked, null);
});

// --- Factory, retail, office ---------------------------------------------------------------
t("factory: production gaps and material movement separate; no vehicles without vehicle cameras", () => {
  const p = siteProfile("factory");
  const daily = { access_windows: [ep("Line A", "08:00", "10:00"), ep("Line A", "11:30", "17:00"), ep("Raw", "09:00", "09:10")], coverage: { gaps: [] } };
  const d = deriveSiteDay({ profile: p, daily, cameras: [cam("Line A", "production_zone"), cam("Raw", "raw_materials")], hours: { open_time: "08:00", close_time: "17:00" }, coverage: VERIFIED });
  assert.equal(d.gaps[0].zone, "production_zone"); assert.equal(d.gaps[0].minutes, 90);
  assert.ok(d.zones.find(z => z.key === "raw_materials").logistics);
  assert.equal(d.vehicles, null);
});
t("retail: checkout eligible only with a configured checkout camera; peak hour from episodes", () => {
  const p = siteProfile("retail");
  const daily = { access_windows: [ep("Door", "11:00", "11:05"), ep("Door", "17:10", "17:12"), ep("Door", "17:20", "17:22"), ep("Till", "17:15", "17:30")] };
  const withTill = deriveSiteDay({ profile: p, daily, cameras: [cam("Door", "entrance"), cam("Till", "checkout_till")], hours: HOURS, coverage: VERIFIED });
  assert.equal(withTill.peakHour, 17);
  assert.equal(eligible(p, withTill, null).checkout, true);
  const noTill = deriveSiteDay({ profile: p, daily, cameras: [cam("Door", "entrance")], hours: HOURS, coverage: VERIFIED });
  assert.equal(eligible(p, noTill, null).checkout, false);
  assert.equal(withTill.gaps.length, 0, "retail does not claim operational gaps");
});
t("office: after-hours only when verified; opening keeps the configured time", () => {
  const p = siteProfile("office");
  const daily = { access_windows: [ep("Front", "09:20", "09:30")], day_boundaries: { opening_at: "09:20", confidence: "high" }, after_hours: { count: 2, verified: false } };
  const d = deriveSiteDay({ profile: p, daily, cameras: [cam("Front", "entrance")], hours: HOURS, coverage: VERIFIED });
  assert.equal(d.afterHours, null);
  assert.equal(d.opening.configured, "09:00");
  const v = deriveSiteDay({ profile: p, daily: { ...daily, after_hours: { count: 2, verified: true } }, cameras: [cam("Front", "entrance")], hours: HOURS, coverage: VERIFIED });
  assert.equal(v.afterHours.count, 2);
});

// --- No data / incomplete capability / denominator zero -------------------------------------
t("no data renders nothing and makes nothing eligible", () => {
  for (const type of ["office", "warehouse", "factory", "retail"]) {
    const p = siteProfile(type);
    const d0 = deriveSiteDay({ profile: p, daily: null, cameras: [], hours: HOURS, coverage: UNKNOWN });
    const d1 = deriveSiteDay({ profile: p, daily: { access_windows: [] }, cameras: [cam("A", "entrance")], hours: HOURS, coverage: VERIFIED });
    for (const d of [d0, d1]) {
      assert.equal(d.timeline.length, 0); assert.equal(d.peakHour, null); assert.equal(d.vehicles, null); assert.equal(d.gaps.length, 0);
      const can = eligible(p, d, null);
      assert.ok(!can.timeline && !can.zones && !can.gaps && !can.vehicles && !can.checkout && !can.comparison, type);
    }
  }
});
t("vehicle episodes without dwell report unknown dwell, never zero", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: { access_windows: [ep("Gate", "09:00", "09:00", { object_class: "vehicle" })] }, cameras: [cam("Gate", "gate")], hours: HOURS, coverage: VERIFIED });
  assert.equal(d.vehicles.episodes, 1);
  assert.equal(d.vehicles.medianDwellMinutes, null);
  assert.equal(d.vehicles.longestDwellMinutes, null);
});
t("cameras with no purpose (incomplete capability) produce no zones", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: whDaily, cameras: whCams.map(c => ({ ...c, purpose: "" })), hours: HOURS, coverage: VERIFIED });
  assert.equal(d.zones.length, 0); assert.equal(d.gaps.length, 0);
  assert.equal(configuredCapabilities(p, { cameras: whCams.map(c => ({ ...c, purpose: "" })), business_context: HOURS }).zones, false);
});
t("unmonitored cameras do not configure a zone", () => {
  const p = siteProfile("warehouse");
  const d = deriveSiteDay({ profile: p, daily: whDaily, cameras: [cam("Dock 1", "loading_dock", false)], hours: HOURS, coverage: VERIFIED });
  assert.equal(d.zones.length, 0);
});

// --- Ask questions follow capability -------------------------------------------------------
t("Ask starters only include questions the configuration can answer", () => {
  const wh = siteProfile("warehouse");
  assert.deepEqual(askQuestions(wh, {}), []);
  const can = configuredCapabilities(wh, { cameras: whCams, business_context: HOURS });
  const qs = askQuestions(wh, can);
  assert.ok(qs.some(q => /quiet period/i.test(q)) && qs.some(q => /loading/i.test(q)));
  assert.ok(!qs.some(q => /gap|delay|downtime|began|start normally/i.test(q)), "observation wording only");
  assert.ok(!qs.some(q => /\b(checkout|tables?|food)\b/i.test(q)));
  const retail = siteProfile("retail");
  assert.ok(!askQuestions(retail, configuredCapabilities(retail, { cameras: [cam("Door", "entrance")], business_context: HOURS })).some(q => /checkout/i.test(q)));
  assert.ok(!askQuestions(retail, configuredCapabilities(retail, { cameras: [cam("Door", "entrance")] })).some(q => /previous/i.test(q)), "no comparison question before the governed comparison exists");
});

// --- Registry: every site type, restaurant included, is selected through one entry point --------
t("selectSiteProfile chooses the composer for all five types deterministically", () => {
  assert.equal(selectSiteProfile({ restaurantConfig: { enabled: true }, contextType: "office" }).composer, "restaurant");
  assert.equal(selectSiteProfile({ restaurantConfig: { enabled: true } }).key, "restaurant");
  const uncalibrated = selectSiteProfile({ contextType: "restaurant" });
  assert.equal(uncalibrated.key, "restaurant"); assert.equal(uncalibrated.composer, "general", "no calibrated config -> no table/service modules");
  for (const k of ["office", "warehouse", "factory", "retail"]) assert.equal(selectSiteProfile({ contextType: k }).composer, "business", k);
  assert.equal(selectSiteProfile({ contextType: "clinic" }).composer, "general");
  assert.equal(selectSiteProfile({ restaurantConfig: { enabled: false }, contextType: "warehouse" }).key, "warehouse");
  assert.equal(selectSiteProfile().composer, "general");
});

// --- Period semantics: a profile only reads its own governed period schema ------------------------
const officePeriod = { enabled: true, schema: "office-period-v1", summary: { activity_detections: 10 } };
const sitePeriod = {
  enabled: true, schema: "site-period-v1",
  summary: { activity_episodes: 9, by_purpose: [{ purpose: "loading_dock", episodes: 5, vehicle_episodes: 1 }, { purpose: "unassigned", episodes: 3 }, { purpose: "checkout", episodes: 2 }] },
  previous_period: { by_purpose: [{ purpose: "loading_dock", episodes: 8 }] },
};
t("warehouse/factory/retail never read office-period-v1; offices never read site-period-v1", () => {
  for (const k of ["warehouse", "factory", "retail"]) {
    assert.equal(acceptPeriod(siteProfile(k), officePeriod), false, k);
    assert.equal(acceptPeriod(siteProfile(k), sitePeriod), true, k);
    assert.equal(periodMeasure(siteProfile(k)).key, "activity_episodes");
    assert.equal(siteProfile(k).period.rpc, "wl_site_period");
  }
  assert.equal(acceptPeriod(siteProfile("office"), sitePeriod), false);
  assert.equal(acceptPeriod(siteProfile("office"), officePeriod), true);
  assert.equal(siteProfile("office").period.rpc, "wl_office_period");
  assert.equal(acceptPeriod(siteProfile("restaurant"), sitePeriod), false);
  assert.equal(acceptPeriod(siteProfile("general"), sitePeriod), false);
  assert.equal(acceptPeriod(siteProfile("warehouse"), { enabled: false, schema: "site-period-v1" }), false);
  assert.equal(eligible(siteProfile("warehouse"), deriveSiteDay({ profile: siteProfile("warehouse"), daily: { access_windows: [] } }), officePeriod).comparison, false);
});
t("period areas are attributed only through configured purposes of the profile", () => {
  const rows = periodAreas(siteProfile("warehouse"), sitePeriod);
  assert.deepEqual(rows.map(r => [r.key, r.current, r.previous, r.delta]), [["loading_dock", 6, 8, -2]], "unassigned and checkout are not warehouse areas");
  assert.deepEqual(periodAreas(siteProfile("warehouse"), officePeriod), []);
});

// --- Starts are observations unless the earlier part of the day was verified ----------------------
const startDay = gaps => ({ access_windows: [ep("Door", "08:43", "09:00")], day_boundaries: { opening_at: "08:43", confidence: "high" }, coverage: { gaps }, meta: { timezone: "Asia/Karachi" } });
t("opening is verified only with full coverage or known gaps that all start after it", () => {
  const p = siteProfile("office"), cams = [cam("Door", "entrance")];
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([]), cameras: cams, hours: HOURS, coverage: VERIFIED }).opening.verified, true);
  assert.equal(deriveSiteDay({ profile: p, daily: startDay(undefined), cameras: cams, hours: HOURS, coverage: PARTIAL }).opening.verified, false, "partial, gaps unknown");
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([]), cameras: cams, hours: HOURS, coverage: PARTIAL }).opening.verified, false, "partial, no gap windows listed");
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([{ start: "07:10", end: "07:40" }]), cameras: cams, hours: HOURS, coverage: PARTIAL }).opening.verified, false, "unverified time before");
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([{ start: "13:00", end: "13:30" }]), cameras: cams, hours: HOURS, coverage: PARTIAL }).opening.verified, true, "unverified time only after");
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([{ start: "??", end: "13:30" }]), cameras: cams, hours: HOURS, coverage: PARTIAL }).opening.verified, false, "unplaceable gap");
  assert.equal(deriveSiteDay({ profile: p, daily: startDay([]), cameras: cams, hours: HOURS, coverage: UNKNOWN }).opening.verified, false);
  const low = { ...startDay([]), day_boundaries: { opening_at: "08:43", low_confidence: true } };
  assert.equal(deriveSiteDay({ profile: p, daily: low, cameras: cams, hours: HOURS, coverage: VERIFIED }).opening.verified, false, "low confidence");
});

// --- The quiet-period threshold is profile configuration, explicitly unvalidated -------------------
t("quiet-period threshold comes from the profile and is marked not field-validated", () => {
  for (const k of ["warehouse", "factory"]) {
    assert.equal(siteProfile(k).quietPeriod.minutes, 45);
    assert.equal(siteProfile(k).quietPeriod.validated, false);
  }
  for (const k of ["office", "retail", "restaurant", "general"]) assert.equal(siteProfile(k).quietPeriod, undefined, k);
  const p = siteProfile("warehouse");
  const daily = { access_windows: [ep("Dock 1", "09:00", "10:00"), ep("Dock 1", "10:50", "17:50")], coverage: { gaps: [] } };
  const d45 = deriveSiteDay({ profile: p, daily, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: VERIFIED });
  assert.equal(d45.quietMinutes, 45); assert.equal(d45.gaps.length, 1);
  const d60 = deriveSiteDay({ profile: { ...p, quietPeriod: { minutes: 60, validated: false } }, daily, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: VERIFIED });
  assert.equal(d60.quietMinutes, 60); assert.equal(d60.gaps.length, 0, "50 min is below a 60 min profile threshold");
});
t("non-office profiles never read the office brief for first/last activity", () => {
  const daily = { access_windows: [ep("Dock 1", "09:10", "09:30")], office: { coverage: { first: "05:00", last: "23:00" } } };
  const wh = deriveSiteDay({ profile: siteProfile("warehouse"), daily, cameras: [cam("Dock 1", "loading_dock")], hours: HOURS, coverage: VERIFIED });
  assert.equal(wh.firstActivity, "09:10"); assert.equal(wh.lastActivity, "09:30");
  const empty = deriveSiteDay({ profile: siteProfile("warehouse"), daily: { access_windows: [], office: daily.office }, cameras: [], hours: HOURS, coverage: VERIFIED });
  assert.equal(empty.firstActivity, null);
});

console.log("site_profiles_unit: " + n + " checks passed");
