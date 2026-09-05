<?php
/* Terms: current SaaS service, pilots, custom work and billing boundaries. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:54rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Terms</span></nav>
    <span class="eyebrow">Terms</span>
    <h1>The commitments on both sides, in plain terms.</h1>
    <p class="lead measure">WatchLog is a Video Analytics &amp; CCTV Intelligence SaaS platform. It supports security and operations teams but does not replace guarding, live monitoring or emergency response.</p>
  </div>
</section>

<section class="surface">
  <div class="wrap"><div class="prose">
    <h2>What the standard service does</h2>
    <p>The current standard product can connect to a supported recorder environment through the WatchLog site software, process selected camera data locally, create incident or analytics records, show Site Health and deliver configured reports through the customer portal and supported delivery channels.</p>

    <h2>What the standard service does not do</h2>
    <p>WatchLog does not guarantee that incidents will be prevented or detected, does not dispatch an emergency response and is not a substitute for guarding, alarms, insurance or a staffed monitoring service. The current product does not provide a live video wall, facial recognition or remote recorded-video archive browsing.</p>

    <h2>Camera analytics and site conditions</h2>
    <p>Analytics quality depends on camera placement, field of view, lighting, occlusion, scene conditions and the rule being measured. A camera view may need calibration and some views may not support a requested metric reliably. Pilot or field acceptance should test the actual site.</p>

    <h2>Compatibility</h2>
    <p>Recorder and camera compatibility is confirmed against the actual environment. A protocol-compatible device or simulated integration is not the same as completed field acceptance on every model.</p>

    <h2>Availability and connectivity</h2>
    <p>If a site's internet connection is interrupted, locally queued data may be sent when connectivity returns. If the recorder, site PC or relevant camera is unavailable, WatchLog may be unable to produce the expected events or measurements for that period.</p>

    <h2>Your responsibilities</h2>
    <ul>
      <li>Keeping the site PC, recorder, cameras and network in a suitable operating condition.</li>
      <li>Having the right to operate cameras and process the data captured in the customer environment.</li>
      <li>Protecting account credentials and controlling who is invited to the customer account.</li>
      <li>Using analytics as operational information rather than as a guaranteed safety, identity or financial-reconciliation system.</li>
    </ul>

    <h2>Pilot and Coming Soon features</h2>
    <p>Control Room, fire and smoke research, broader object analytics and other roadmap items may be described as Pilot, Coming Soon or research. Those labels mean the capability is not part of the standard available product unless a written pilot or contract says otherwise.</p>

    <h2>Custom Solution work</h2>
    <p>POS, Shopify, attendance, CRM and other integrations or industry-specific workflows can be scoped separately. Custom work may have its own milestones, acceptance criteria, fees, support terms and data responsibilities. A custom-solution discussion does not mean a finished connector already exists.</p>

    <h2>Billing</h2>
    <p>Standard Starter and Growth subscriptions are monthly per site, in advance. Enterprise, pilots and custom implementations may use a quotation, invoice or contract. The current standard trial is fourteen days with no card. A lapsed trial or subscription pauses reporting.</p>

    <h2>Cancellation and refunds</h2>
    <p>Under the current standard monthly terms, cancellation stops future renewal after the already-paid monthly period. The definitive refund rule still requires business-owner approval and is published separately on the <a href="<?php echo watchlog_url('refund-cancellation-service-delivery'); ?>">Refund, Cancellation &amp; Service Delivery</a> page. WatchLog will not invent a refund commitment before that approval.</p>

    <h2>Payments</h2>
    <p>Payment instructions shown on an invoice or enabled checkout are the applicable payment method for that order. Manual bank transfer may be used during rollout. The website should only present an online gateway as available after it is actually enabled.</p>

    <h2>Support and after-sales</h2>
    <p>Support may include help with site connection, camera-system status, analytics configuration and expansion of an existing deployment. Custom implementation support should be agreed in the relevant proposal or contract.</p>

    <h2>Data and privacy</h2>
    <p>Use of the service is also subject to the <a href="<?php echo watchlog_url('privacy'); ?>">Privacy</a> page, which describes the current data boundary and account information handled by WatchLog.</p>
  </div></div>
</section>
<?php get_footer(); ?>
