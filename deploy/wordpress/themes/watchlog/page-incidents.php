<?php
/* Incidents: event vs incident, on-site filtering, real Incidents UI. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:54rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Incidents</span></nav>
    <span class="eyebrow">Incidents</span>
    <h1>From motion to something worth reviewing.</h1>
    <p class="lead measure">Your recorder logs hundreds of events a night. WatchLog keeps only the ones
      that matter, each with a still, so what reaches your portal is worth opening.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
  </div>
  <div class="wrap-wide glow" style="margin-top:clamp(28px,4vw,48px)">
    <?php echo watchlog_shot('product-incidents','WatchLog Incidents: filters, incident list and a selected person incident with its still',1900,1150,'(max-width:1200px) 92vw, 1100px', true, true); ?>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">The distinction that matters</span>
      <h2>An event is not an incident.</h2>
      <p class="lead measure">The recorder produces <strong>events</strong> for motion of any kind. WatchLog
        keeps only the ones with a person, car or motorcycle, and those become <strong>incidents</strong>.</p></div>
    <div class="grid g2" style="max-width:920px;margin-inline:auto">
      <div class="note-card"><h3 style="margin-top:0">Event</h3><p style="margin-bottom:0">A raw record from the recorder: motion on a camera at a time. Rain, headlights, the IR lamp at dusk, a cat are all events, most of them noise.</p></div>
      <div class="note-card"><h3 style="margin-top:0">Incident</h3><p style="margin-bottom:0">A validated event that passed on-site filtering by showing a person, car or motorcycle. Only incidents sync to WatchLog, each with one still.</p></div>
    </div>
  </div>
</section>

<section class="dark field ai">
  <div class="wrap-wide">
    <div class="sec-head center"><span class="eyebrow">Filtered on site</span>
      <h2>Kept, or dropped, before anything is sent.</h2></div>
    <div style="max-width:1100px;margin:0 auto"><?php echo watchlog_pic('diagram-ai-filtering','Raw event to on-site AI to a kept, validated incident; fail-open if the detector cannot run',1800,1000,'',' (max-width:1100px) 92vw, 1100px'); ?></div>
    <div class="ai-strip">
      <div class="ai-col"><span class="ai-lbl kept">Kept as incidents</span>
        <div class="ai-thumbs">
          <?php echo watchlog_pic('cctv-person','Example CCTV still: a person',1600,900,'','30vw'); ?>
          <?php echo watchlog_pic('cctv-vehicle','Example CCTV still: a vehicle',1600,900,'','30vw'); ?>
          <?php echo watchlog_pic('cctv-motorcycle','Example CCTV still: a motorcycle',1600,900,'','30vw'); ?>
        </div>
      </div>
      <div class="ai-col"><span class="ai-lbl dropped">Filtered out</span>
        <div class="ai-thumbs">
          <?php echo watchlog_pic('cctv-empty-rain','Example CCTV still: empty scene in rain',1600,900,'','30vw'); ?>
          <?php echo watchlog_pic('cctv-headlights','Example CCTV still: headlights on a wall',1600,900,'','30vw'); ?>
        </div>
      </div>
    </div>
    <p class="center note-line">Detects person, car and motorcycle. Not facial recognition. WatchLog reports incidents. It does not watch live or dispatch a response.</p>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">In the portal</span><h2>Search the history, open the moment.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Every incident has a still</h3><p>The frame from the moment it happened, attached to the record.</p></div>
      <div class="card"><?php echo watchlog_icon('chart',24); ?><h3>Filter by site, camera, type</h3><p>Narrow to a camera, a window of time, or a kind of incident.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After-hours at a glance</h3><p>The count that gets read first, separated out for you.</p></div>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Stop scrubbing footage. Start reading incidents.</h2>
    <p class="lead measure">Works with the recorder you already own.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('reporting'); ?>">See reporting</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
