<?php
/* Solution: Quick-Service Restaurants. Current capability and pilot scope separated. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-retail','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Quick-Service Restaurants</span></nav>
      <span class="eyebrow">Quick-Service Restaurants</span>
      <h1>Turn branch cameras into daily operational visibility.</h1>
      <p class="lead">Use configured camera views for people flow, queue and checkout-zone activity, after-hours movement, site health and branch reporting. Multi-site Control Room workflows are the next pilot layer, not an available live video wall today.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a QSR pilot</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-retail','A quick-service restaurant style retail environment',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Available today</span><h2>Start with measurable branch questions.</h2></div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Available</span><h3>People flow</h3><p>Measure configured movement through entrances, exits and selected service areas. No facial recognition.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Queue and checkout-zone activity</h3><p>Measure activity or occupancy in configured zones. This is not exact transactions, sales or till reconciliation.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>After-hours activity</h3><p>Use schedules to separate expected operating activity from movement outside configured hours.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Site Health</h3><p>See whether the site, recorder and cameras are still reporting instead of discovering a blind spot later.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Branch reporting</h3><p>Send scheduled operational summaries by email, WhatsApp or both, with delivery history in the portal.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Multi-site visibility</h3><p>Bring branch incidents, health and analytics into one SaaS account with customer and site separation.</p></div>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Pilot / Coming Soon</span><h2>Control Room for multi-branch operations.</h2>
      <p class="lead measure">The planned pilot focuses on a central operational workspace for site status, event review, branch-level and collective reporting, and multi-camera layout research.</p></div>
    <div class="grid g3">
      <div class="card"><h3>Branch status</h3><p>Bring connection and camera-system health into a central operational queue.</p></div>
      <div class="card"><h3>Event review</h3><p>Prioritize incidents and analytics signals without representing the module as continuous live monitoring.</p></div>
      <div class="card"><h3>Camera layout research</h3><p>Validate which camera views can reliably support the operational measurements required by the branch.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Connect operations data when the pilot needs it.</h2>
      <p>POS, CRM, attendance or other store systems can be scoped as customer-specific integrations. WatchLog does not advertise a finished POS connector today.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('integrations'); ?>">Explore integration options <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="note-card">
      <h3>Camera placement matters.</h3>
      <p>Queue, entrance and service-area measurements depend on field of view, lighting, occlusion and the placement of the existing camera. Pilot acceptance should validate the actual branch rather than assume every camera angle can support the same metric.</p>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center"><h2>Plan the first branch pilot around a real operational question.</h2>
    <div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss the pilot</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
  </div>
</section>
<?php get_footer(); ?>
