<?php
/* Solution: Fuel & Forecourt. Custom solution positioning only. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-office-commercial','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Fuel &amp; Forecourt</span></nav>
      <span class="eyebrow">Custom Solution</span>
      <h1>Apply camera intelligence to forecourt operations without pretending every site is the same.</h1>
      <p class="lead">Fuel and forecourt use cases can combine current people and vehicle flow, configured zones, after-hours activity, site health and tailored reporting. More specialized workflows should be scoped and validated as customer-specific work.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a forecourt use case</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See current capabilities</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-office-commercial','A commercial site environment used as a generic operations visual',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Current building blocks</span><h2>Use available analytics where the camera view supports them.</h2></div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Available</span><h3>Vehicle flow</h3><p>Measure configured vehicle entries, exits and movement. This is anonymous flow measurement, not ANPR or vehicle identity.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>People and zone activity</h3><p>Measure configured movement and dwell around selected operational areas without facial recognition.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Site Health</h3><p>Track whether the site, recorder and cameras are still reporting so operational blind spots are visible.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Specialized workflows need a scoped pilot.</h2>
      <p>Examples may include tailored reporting, integration with a customer system, site-specific operational zones or workflow automation. These should not be represented as standard product features until built and accepted.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('integrations'); ?>">See custom integration directions <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="note-card"><h3>Camera layout first.</h3><p>Forecourt views can have glare, occlusion, distance and changing traffic patterns. Pilot acceptance should confirm what each camera can reliably measure before the metric is used operationally.</p></div>
  </div>
</section>
<?php get_footer(); ?>
