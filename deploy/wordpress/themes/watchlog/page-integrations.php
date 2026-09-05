<?php
/* Integrations: customer-specific scope only until connectors are validated and productized. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:58rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Integrations</span></nav>
    <span class="eyebrow">Custom Solution</span>
    <h1>Connect camera intelligence to the systems your operation already uses.</h1>
    <p class="lead measure">WatchLog can scope customer-specific integrations for POS systems, Shopify, attendance machines, CRM and sales-pipeline systems. These are not advertised as finished plug-and-play connectors today.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss an integration</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See current capabilities</a></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Integration directions</span><h2>Scope the data flow around the business outcome.</h2>
      <p class="lead measure">A useful integration starts by defining what is measured, which system is authoritative, what should be automated and what needs human verification.</p></div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>POS systems</h3><p>Explore event or operational context around configured service areas without claiming camera analytics can infer exact transactions or reconcile tills on their own.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>Shopify</h3><p>Scope warehouse or stock workflows where camera-derived activity and Shopify data may support an operational process. No finished stock-sync connector is presented as available.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>Attendance machines</h3><p>Integrate with an attendance system where appropriate. Current anonymous people-flow analytics are separate from employee identity and attendance records.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>CRM and sales pipeline</h3><p>Send approved operational signals or customer-specific workflow events into a CRM after the required schema, permissions and business rules are defined.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>Reporting workflows</h3><p>Combine WatchLog analytics with external operational data when a customer needs a tailored report rather than a standard product report.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>Industry APIs</h3><p>Assess other customer systems through a scoped implementation. Availability depends on the external system, API access and acceptance testing.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">How custom work is handled</span>
      <h2>Discovery first. Build second.</h2>
      <p>Custom work should define the source systems, data ownership, security boundary, failure handling, acceptance criteria and ongoing support before implementation starts.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('check',20); ?> Confirm the operational question and expected output</li>
        <li><?php echo watchlog_icon('lock',20); ?> Review credentials, API access and data boundaries</li>
        <li><?php echo watchlog_icon('chart',20); ?> Define measurable pilot acceptance criteria</li>
        <li><?php echo watchlog_icon('people',20); ?> Agree support and after-sales ownership</li>
      </ul>
    </div>
    <div class="note-card">
      <h3>No hidden partnership claims.</h3>
      <p>Technology vendors, cloud providers and camera manufacturers should only be presented as partners after the relationship is formally approved. Compatibility or use of a technology is not the same as a partnership.</p>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center"><h2>Have a system you want WatchLog to work with?</h2><p class="lead measure">Tell us the system, the data available and the outcome you need.</p><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a custom solution</a></div>
</section>
<?php get_footer(); ?>
