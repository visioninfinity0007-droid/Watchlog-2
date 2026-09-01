<?php
/* Site Health: the quiet feature that earns its keep. No over-promised alerts. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Site Health</span></nav>
      <span class="eyebrow">Site Health</span>
      <h1>A silent camera shouldn't stay silent for weeks.</h1>
      <p class="lead">The camera you rely on is usually the one that stopped working a month ago,
        discovered on the day you need its footage. WatchLog surfaces that far sooner.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_shot('product-site-health','WatchLog Site Health: a healthy site and a camera fault',1500,1050,'(max-width:900px) 92vw, 52vw',true); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What it watches</span><h2>The signals that a site is really working.</h2></div>
    <div class="grid g2">
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Camera last seen</h3><p>When each camera last produced anything. A camera that's gone quiet stands out instead of blending in.</p></div>
      <div class="card"><?php echo watchlog_icon('recorder',24); ?><h3>Recorder &amp; camera faults</h3><p>Faults the recorder itself reports, including tamper, are surfaced rather than buried in a log.</p></div>
      <div class="card"><?php echo watchlog_icon('pc',24); ?><h3>Agent reporting status</h3><p>If the site program itself stops reporting, the site shows as quiet, so you find out the software went down, not just the cameras.</p></div>
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Per-site status</h3><p>Every location's health at a glance, so one bad site doesn't hide behind the others.</p></div>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Why it matters</span>
      <h2>Recording you can't rely on isn't security.</h2>
      <p>A blind spot on a loading bay or a gate is a real gap, but only if you know about it. Site Health
        turns "we thought that camera was recording" into "we were told the day it stopped."</p>
      <ul class="ticks light-ticks">
        <li><?php echo watchlog_icon('alert',20); ?> Silent cameras surfaced in the portal and daily report</li>
        <li><?php echo watchlog_icon('clock',20); ?> Last-seen times so gaps are obvious</li>
        <li><?php echo watchlog_icon('check',20); ?> Confirmation, most days, that everything is working</li>
      </ul>
    </div>
    <div><?php echo watchlog_pic('cctv-camera-offline','An example of a camera that has gone offline',1600,900,'frame-plain','(max-width:900px) 92vw, 48vw'); ?></div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="note-card" style="max-width:46rem;margin-inline:auto;text-align:center">
      <p style="margin:0"><strong>Honest about how you're told.</strong> Site Health appears in your portal
        and in the daily report. WatchLog is a daily reporting service. It flags what has gone quiet each
        day rather than paging you the instant a camera drops.</p>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Find the dead camera before you need it.</h2>
    <p class="lead measure">Start free for 14 days. Keep your cameras and recorder.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
