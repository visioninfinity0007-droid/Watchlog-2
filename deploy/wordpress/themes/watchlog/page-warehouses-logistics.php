<?php
/* Solution: Warehouses & Logistics. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-warehouse','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Warehouses &amp; Logistics</span></nav>
      <span class="eyebrow">Warehouses &amp; Logistics</span>
      <h1>Use existing cameras to understand gates, loading areas and after-hours activity.</h1>
      <p class="lead">WatchLog can combine current people and vehicle flow, configured zones, dwell, incident intelligence, Site Health and reporting across warehouse and logistics locations. Stock-system automation remains a Custom Solution rather than a standard connector.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a rollout</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-warehouse','A logistics warehouse yard at dusk',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Available today</span><h2>Measure the operational areas the cameras can actually see.</h2></div>
    <div class="grid g3">
      <div class="card"><h3>Loading-area activity</h3><p>Use configured zones and schedules to understand activity around selected loading or dispatch areas.</p></div>
      <div class="card"><h3>Vehicle flow</h3><p>Measure configured vehicle entries, exits and movement without claiming ANPR or vehicle identity.</p></div>
      <div class="card"><h3>People flow</h3><p>Measure configured movement through entrances, exits and operational boundaries without facial recognition.</p></div>
      <div class="card"><h3>Dwell / time in zone</h3><p>Track how long activity remains in a configured area using adjustable dwell thresholds.</p></div>
      <div class="card"><h3>After-hours activity</h3><p>Use site schedules to separate expected operations from movement outside configured hours.</p></div>
      <div class="card"><h3>Site Health &amp; reporting</h3><p>Keep recorder and camera-system visibility alongside daily or scheduled operational summaries.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Stock and Shopify workflows need system integration, not camera guesswork.</h2>
      <p>Where a warehouse wants stock updates or Shopify-connected operations, WatchLog can scope a customer-specific workflow that combines approved camera-derived activity with authoritative business-system data. The website does not claim an automatic stock-count or finished Shopify connector today.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('integrations'); ?>">Explore integrations <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="note-card"><h3>Camera layout affects accuracy.</h3><p>Loading bays and yards may have distance, occlusion, headlights, weather and changing traffic patterns. Pilot acceptance should validate each selected camera view under real site conditions.</p></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center"><h2>Build the rollout around measurable warehouse questions.</h2><p class="lead measure">Start with a real recorder and the actual camera views.</p><div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a custom workflow</a></div></div>
</section>
<?php get_footer(); ?>
