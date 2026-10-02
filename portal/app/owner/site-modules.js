"use client";
// Site-type intelligence modules (Phase 28). Shared by Home, Insights and Reports so every site type
// stays one WatchLog product. Each module renders only when its profile makes it relevant AND the
// governed data makes it eligible (see site-profiles.js). Facts are deterministic; recommended next
// steps suggest an investigation and never assert a cause.
import { Section, Row, Metrics, Bars, HBars, Empty, RailSection, Stat } from "./ui";
import { withSite } from "../site-context";
import { minutesLabel } from "./site-profiles";

function hourLabel(h) {
  const v = ((h % 24) + 24) % 24;
  return (v % 12 || 12) + (v < 12 ? " am" : " pm");
}
function hourRange(h) { return hourLabel(h) + "–" + hourLabel(h + 1); }

const ZONE_TITLE = { office: "Area activity", warehouse: "Dock and area comparison", factory: "Production and site areas", retail: "Store areas", general: "Area activity" };
const ZONE_QUESTION = { office: "Which areas were used, and for how long?", warehouse: "Which loading and storage areas were active?", factory: "Which production areas were active, and for how long?", retail: "Which store areas attracted the most activity?", general: "Which areas were active?" };
// Investigation prompts per operational area. They suggest what to check; they never state a cause.
const GAP_ACTION = {
  warehouse: "Check whether this quiet period was planned or whether work moved to another area.",
  factory: "Check whether this was a planned break, changeover or maintenance.",
};
const GAP_ACTION_ZONE = {
  loading_dock: "Check whether loading was scheduled for this window or moved to another dock.",
  dispatch: "Check whether dispatch was scheduled for this window or moved to another lane.",
  receiving: "Check whether a delivery was expected in this window.",
};

// One-sentence operational conclusion from governed facts only (no cause, no counts of people).
export function operationalLead(profile, day, can) {
  if (!day) return null;
  if (profile.key === "office") {
    const o = startStatement(profile, day);
    if (!o) return null;
    const late = day.opening?.verified && day.opening.configured && toMin(day.opening.at) - toMin(day.opening.configured) > 15;
    return o + (late ? ", later than the configured " + day.opening.configured + " opening" : "");
  }
  if (profile.key === "warehouse" || profile.key === "factory") {
    if (day.gaps.length) {
      const g = day.gaps[0];
      return "Notable quiet period at " + g.label.toLowerCase() + ": " + minutesLabel(g.minutes) + " with no observed activity (" + g.start + "–" + g.end + ")";
    }
    const op = day.zones.filter(z => (z.operational || z.logistics) && z.episodes > 0).sort((a, b) => b.activeMinutes - a.activeMinutes)[0];
    if (op && profile.key === "warehouse") return "Loading and dispatch activity was concentrated at " + op.label.toLowerCase();
    return startStatement(profile, day);
  }
  if (profile.key === "retail") {
    if (day.peakHour !== null) return "Store activity peaked " + hourRange(day.peakHour);
    return startStatement(profile, day);
  }
  return null;
}

// Observed first event is not an operational start. "began" only when the earlier part of the day was
// verified (day.opening.verified); otherwise "first observed".
const START_NOUN = { office: "Office activity", warehouse: "Site activity", factory: "Production-area activity", retail: "Store activity" };
export function startStatement(profile, day) {
  const noun = START_NOUN[profile.key] || "Activity";
  if (day.opening?.at && day.opening.verified) return noun + " began at " + day.opening.at;
  const first = day.opening?.at || day.firstActivity;
  return first ? "First observed " + noun.toLowerCase() + " was at " + first : null;
}
function toMin(hhmm) { const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || "")); return m ? Number(m[1]) * 60 + Number(m[2]) : 0; }

export function SiteTimeline({ profile, day, first }) {
  if (!day.timeline.length) return null;
  // Warehouses and factories run long continuous work, so the hour shows how much of it operational areas
  // were active; offices and stores show activity episodes per hour.
  const op = (profile.gapZones || []).length > 0 && day.timeline.some(t => t.operationalShare !== null);
  const peak = day.peakHour;
  return <Section first={first} title={profile.operationsTitle || "Activity through the day"} note={op ? "Share of each hour that operational areas were active, averaged across areas" : "Activity episodes per hour from configured cameras · not unique people"}>
    <Bars question={op ? "When were operational areas active?" : "When was " + (profile.activityNoun || "activity") + " highest?"} height={110}
      series={day.timeline.map(t => ({ label: hourLabel(t.hour).replace(" ", ""), value: op ? t.operationalShare || 0 : t.episodes, display: op ? (t.operationalShare || 0) + "%" : undefined, peak: !op && t.hour === peak, title: hourRange(t.hour) + ": " + (op ? (t.operationalShare || 0) + "% of the hour active" : t.episodes + " episodes") }))}
      legend={<span>{op ? "Operational areas active (% of hour)" : "Activity episodes"}</span>} />
    {peak !== null && <p className="ow-muted" style={{ fontSize: 12.5, marginTop: 8 }}>{op ? "Busiest hour for episodes" : "Peak"}: <b style={{ color: "var(--ow-ink)" }}>{hourRange(peak)}</b>{day.firstActivity ? " · first observed " + day.firstActivity : ""}{day.lastActivity ? " · last " + day.lastActivity : ""}</p>}
  </Section>;
}

function episodesLabel(n) { return n + (n === 1 ? " episode" : " episodes"); }
export function SiteZones({ profile, day, filter, title, question }) {
  const zones = day.zones.filter(z => (filter ? filter(z) : true));
  if (!zones.some(z => z.episodes > 0)) return null;
  const quiet = zones.filter(z => z.episodes === 0);
  return <Section title={title || ZONE_TITLE[profile.key] || "Area activity"} note="Active time from governed activity episodes, by configured camera purpose">
    <HBars question={question || ZONE_QUESTION[profile.key]} items={zones.filter(z => z.episodes > 0).sort((a, b) => b.activeMinutes - a.activeMinutes).map(z => ({ key: z.key, label: z.label, note: (z.first && z.last ? z.first + "–" + z.last + " · " : "") + episodesLabel(z.episodes) + (z.vehicles ? " (" + z.vehicles + " vehicle)" : ""), value: z.activeMinutes }))} />
    <p className="ow-muted" style={{ fontSize: 12, marginTop: 8 }}>Bars show active minutes.{quiet.length ? " No activity observed at: " + quiet.map(z => z.label).join(", ") + "." : ""}</p>
  </Section>;
}

export function SiteGaps({ profile, day, siteId }) {
  if (!(profile.gapZones || []).length) return null;
  if (!day.gaps.length && !day.gapsBlocked) return null;
  const threshold = day.quietMinutes || profile.quietPeriod?.minutes || 45;
  return <Section title="Notable quiet periods" count={day.gaps.length || null} note={"No observed activity for " + threshold + " min or longer within configured hours · an observation, not a finding of downtime or delay"}>
    {day.gaps.length ? <div className="ow-rows">{day.gaps.slice(0, 4).map(g => <Row key={g.zone + g.start} tone="warn" title={g.label + " · " + minutesLabel(g.minutes) + " with no observed activity"} body={(profile.key === "warehouse" && GAP_ACTION_ZONE[g.zone]) || GAP_ACTION[profile.key] || "Check whether this was expected."} meta={[g.start + "–" + g.end + " · site time", "Cause not known"]} action={<a href={withSite("/ai/?prompt=" + encodeURIComponent("What happened around the " + g.label.toLowerCase() + " between " + g.start + " and " + g.end + "?"), siteId)}>Ask WatchLog</a>} />)}</div>
      : <Empty title="Quiet periods can't be confirmed for this day.">{day.gapsBlocked?.reason === "overlap" ? "The quiet period overlaps time WatchLog could not verify." : "Part of the day could not be verified, so a quiet period may be unverified time."}</Empty>}
  </Section>;
}

const VEHICLE_SCOPE = { warehouse: "gate, yard and dock cameras", factory: "gate, store and dispatch cameras" };
export function SiteVehicles({ profile, day }) {
  if (!day.vehicles) return null;
  const v = day.vehicles;
  return <Section title="Vehicle movement" note={"Vehicle episodes at " + (VEHICLE_SCOPE[profile?.key] || "configured vehicle cameras") + " · not separate vehicles, not time on site"}>
    <Metrics items={[
      { value: String(v.episodes), label: "Vehicle episodes", primary: true },
      { value: v.medianDwellMinutes === null ? null : minutesLabel(v.medianDwellMinutes), label: "Median time at a camera", unknown: "Not available" },
      { value: v.longestDwellMinutes === null ? null : minutesLabel(v.longestDwellMinutes), label: "Longest stay at a camera", note: v.longest ? (v.longest.camera || "") + (v.longest.start ? " · from " + v.longest.start : "") : undefined, unknown: "Not available" },
    ]} />
  </Section>;
}

export function SiteCheckout({ day }) {
  const checkout = day.zones.find(z => z.key === "checkout");
  const entrance = day.zones.find(z => z.key === "entrance");
  if (!checkout) return null;
  return <Section title="Checkout activity" note="Observed activity only · no sales, transactions or conversion are inferred">
    <Metrics items={[
      { value: checkout.episodes ? minutesLabel(checkout.activeMinutes) : null, label: "Checkout active time", note: checkout.first ? checkout.first + "–" + checkout.last : undefined, primary: true, unknown: "No activity observed" },
      { value: String(checkout.episodes), label: "Checkout episodes" },
      ...(entrance ? [{ value: String(entrance.episodes), label: "Entrance episodes", note: "for comparison with checkout" }] : []),
    ]} />
  </Section>;
}

export function SiteAfterHours({ day, siteId }) {
  if (!day.afterHours) return null;
  const n = day.afterHours.count;
  return <Section title="After-hours activity" count={n || null}>
    {n ? <div className="ow-rows"><Row tone="warn" title={n + " activity " + (n === 1 ? "episode" : "episodes") + " outside configured hours"} body="Review whether each was expected." action={<a href={withSite("/notifications/", siteId)}>Review</a>} /></div>
      : <Empty title="No after-hours activity was observed.">Only time with monitoring coverage is counted.</Empty>}
  </Section>;
}

// The owner's operating story for a site type, in decision order.
export function SiteOperations({ profile, day, siteId, first, include }) {
  if (!day) return null;
  const want = k => !include || include.includes(k);
  return <>
    {want("timeline") && <SiteTimeline profile={profile} day={day} first={first} />}
    {want("zones") && <SiteZones profile={profile} day={day} filter={profile.key === "factory" ? z => !z.logistics : undefined} />}
    {want("gaps") && <SiteGaps profile={profile} day={day} siteId={siteId} />}
    {want("logistics") && profile.key === "factory" && <SiteZones profile={profile} day={day} filter={z => z.logistics} title="Material movement" question="When did material stores and dispatch see activity?" />}
    {want("vehicles") && (profile.key === "warehouse" || profile.key === "factory") && <SiteVehicles profile={profile} day={day} />}
    {want("checkout") && profile.key === "retail" && <SiteCheckout day={day} />}
    {want("afterHours") && profile.key === "office" && <SiteAfterHours day={day} siteId={siteId} />}
  </>;
}

// Compact rail facts for the site type.
export function SiteRailFacts({ profile, day, label }) {
  if (!day) return null;
  const top = day.zones.filter(z => z.episodes > 0).sort((a, b) => b.activeMinutes - a.activeMinutes)[0];
  const facts = [];
  if (day.opening?.at || day.firstActivity) facts.push(<Stat key="first" label={day.opening?.verified ? (profile.key === "factory" ? "Shift activity began" : "Activity began") : "First observed activity"} value={day.opening?.at || day.firstActivity} note={day.opening?.configured ? "configured " + day.opening.configured : null} />);
  if (top) facts.push(<Stat key="top" label="Most active area" value={top.label} note={minutesLabel(top.activeMinutes) + " active"} />);
  if (profile.key === "retail" && day.peakHour !== null) facts.push(<Stat key="peak" label="Peak hour" value={hourRange(day.peakHour)} />);
  if ((profile.gapZones || []).length && day.gaps.length) facts.push(<Stat key="gap" label="Longest quiet period" value={minutesLabel(day.gaps[0].minutes)} note={day.gaps[0].label} />);
  if (day.vehicles) facts.push(<Stat key="veh" label="Vehicle episodes" value={String(day.vehicles.episodes)} />);
  if (day.afterHours) facts.push(<Stat key="ah" label="After-hours episodes" value={String(day.afterHours.count)} />);
  if (!facts.length) return null;
  return <RailSection label={label || profile.label + " · today"}>{facts}</RailSection>;
}
