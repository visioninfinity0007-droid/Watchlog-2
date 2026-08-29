<?php
/**
 * Features.
 *
 * Grouped by what the reader is trying to find out, not by how the
 * software is built. Each block leads with the icon and says what the
 * feature is FOR — a list of capabilities with no "so that" is a
 * specification sheet, and nobody buys from one.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = watchlog_signup_url();
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>Features
    </nav>
    <h1>What you get, and what each thing is for</h1>
    <p class="lede">Five things. Each one exists because a customer could not
      do something without it.</p>
  </div>
</section>

<div class="wrap">

  <section class="feature-block">
    <div class="feature-head">
      <span class="ico-badge"><?php echo watchlog_icon('report'); ?></span>
      <div>
        <h2>Daily reporting</h2>
        <p class="lede">The product, really. Everything else supports this.</p>
      </div>
    </div>
    <div class="grid g2">
      <ul class="checklist">
        <li><?php echo watchlog_icon('check', 19); ?><span>A morning summary on WhatsApp, and in the portal</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Counts by camera and by type</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>How many events fell after hours</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>First and last event time, in the site's own timezone</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Per-site recipients — the branch manager gets their branch</span></li>
      </ul>
      <div class="card">
        <p><strong>Why the timezone matters.</strong> "Yesterday" has to mean
          yesterday where the cameras are. A site in Karachi and a server in
          UTC disagree for five hours every night — which is exactly the
          window most incidents fall into.</p>
      </div>
    </div>
  </section>

  <section class="feature-block">
    <div class="feature-head">
      <span class="ico-badge"><?php echo watchlog_icon('camera'); ?></span>
      <div>
        <h2>Incidents</h2>
        <p class="lede">Every event logged with a still from the moment it
          happened.</p>
      </div>
    </div>
    <div class="grid g2">
      <ul class="checklist">
        <li><?php echo watchlog_icon('check', 19); ?><span>A still image captured at the moment of the event</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Filtered on site, so what you read is worth reading</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Searchable history in the portal</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Person, vehicle and motorcycle recognised and labelled</span></li>
      </ul>
      <div class="card">
        <p><strong>Filtering happens on your machine.</strong> Rain, headlights
          on a wall, a cat, the infrared lamp cutting in at dusk — all motion,
          none of it an incident. Frames without a person or vehicle are
          discarded at the site and never leave the building.</p>
      </div>
    </div>
  </section>

  <section class="feature-block">
    <div class="feature-head">
      <span class="ico-badge warn"><?php echo watchlog_icon('alert'); ?></span>
      <div>
        <h2>Site health</h2>
        <p class="lede">The quiet one that earns its keep.</p>
      </div>
    </div>
    <div class="grid g2">
      <ul class="checklist">
        <li><?php echo watchlog_icon('check', 19); ?><span>Cameras that have gone silent, reported as faults</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Recorder faults and tamper events</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>An alert if the site program itself stops reporting</span></li>
        <li><?php echo watchlog_icon('check', 19); ?><span>Last-seen time for every camera</span></li>
      </ul>
      <div class="card">
        <p><strong>A camera that stopped working three weeks ago is normally
          discovered on the day you need its footage.</strong> This is the
          feature customers rarely ask for and then tell other people
          about.</p>
      </div>
    </div>
  </section>

  <section class="feature-block">
    <div class="feature-head">
      <span class="ico-badge"><?php echo watchlog_icon('chart'); ?></span>
      <div>
        <h2>Analytics</h2>
        <p class="lede">Patterns, not just yesterday.</p>
      </div>
    </div>
    <ul class="checklist" style="grid-template-columns:repeat(2,1fr);display:grid">
      <li><?php echo watchlog_icon('check', 19); ?><span>Busiest cameras and busiest hours</span></li>
      <li><?php echo watchlog_icon('check', 19); ?><span>Activity by hour in the site's own timezone</span></li>
      <li><?php echo watchlog_icon('check', 19); ?><span>Trends across 7, 30 or 90 days</span></li>
      <li><?php echo watchlog_icon('check', 19); ?><span>After-hours share, tracked over time</span></li>
    </ul>
  </section>

  <section class="feature-block">
    <div class="feature-head">
      <span class="ico-badge"><?php echo watchlog_icon('people'); ?></span>
      <div>
        <h2>More than one site, more than one person</h2>
        <p class="lede">Built in from the start, not bolted on.</p>
      </div>
    </div>
    <div class="grid g3">
      <div class="card">
        <span class="ico-badge"><?php echo watchlog_icon('sites'); ?></span>
        <h3>Every location</h3>
        <p>One view across all of them, and each site's own report to the
          person who runs it.</p>
      </div>
      <div class="card">
        <span class="ico-badge"><?php echo watchlog_icon('people'); ?></span>
        <h3>Roles</h3>
        <p>Owner, admin or read-only. The control room can look without being
          able to change anything.</p>
      </div>
      <div class="card">
        <span class="ico-badge"><?php echo watchlog_icon('lock'); ?></span>
        <h3>Separated</h3>
        <p>Your data is isolated from every other customer at the database
          level, and an automated test proves it before each release.</p>
      </div>
    </div>
  </section>

</div>

<section class="cta-band">
  <div class="wrap center">
    <h2>See it on your own cameras</h2>
    <p class="lede center-lede">Fourteen days, no card. Ten minutes to install.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/how-it-works/">How it works</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
