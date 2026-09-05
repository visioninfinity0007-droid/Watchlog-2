<?php
/* Solution: Manufacturing & Textiles. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-manufacturing','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Manufacturing &amp; Textiles</span></nav>
      <span class="eyebrow">Manufacturing &amp; Textiles</span>
      <h1>Turn plant cameras into configured operational measurements.</h1>
      <p class="lead">Use current people and vehicle flow, zones, dwell, after-hours activity, incident intelligence, Site Health and reporting around gates, loading areas and selected production spaces. Machine-to-machine or stock workflows require separate system integration.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a manufacturing use case</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-manufacturing','A manufacturing plant floor',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Available today</span><h2>Configure analytics around the areas that matter.</h2></div>
    <div class="grid g3">
      <div class="card"><h3>Gate and entry flow</h3><p>Measure configured people or vehicle movement through selected entrances, exits and boundaries.</p></div>
      <div class="card"><h3>Zones and dwell</h3><p>Measure activity and time in configured operational areas without implying worker identity.</p></div>
      <div class="card"><h3>Shift schedules</h3><p>Use configured schedules to compare expected operating periods with activity outside those hours.</p></div>
      <div class="card"><h3>Vehicle movement</h3><p>Measure anonymous vehicle flow around selected gates and operational areas, not ANPR or fleet identity.</p></div>
      <div class="card"><h3>Incident intelligence</h3><p>Use current detector classes and on-site filtering to surface selected incident events for review.</p></div>
      <div class="card"><h3>Site Health</h3><p>Keep visibility into whether the recorder, site connection and cameras are still reporting.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Machine, stock and business-system workflows need explicit integration.</h2>
      <p>For textile or manufacturing customers that need machine-to-machine data, stock monitoring, ERP-style workflows or another operational system, WatchLog can scope a customer-specific integration. Camera activity should support that workflow, not be presented as a replacement for authoritative machine or inventory data.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('integrations'); ?>">See integration directions <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="note-card"><h3>Validate the camera view.</h3><p>Production-floor analytics can be affected by occlusion, distance, lighting, machine movement and changing layouts. Pilot acceptance should test the real scene and the exact operational metric before rollout.</p></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center"><h2>Start with one measurable plant question.</h2><p class="lead measure">Then decide what belongs in the standard product and what needs a custom workflow.</p><div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a custom solution</a></div></div>
</section>
<?php get_footer(); ?>
