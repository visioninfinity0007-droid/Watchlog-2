<?php
/* Privacy: current data boundary, account data and merchant-facing processing. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:54rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Privacy</span></nav>
    <span class="eyebrow">Privacy</span>
    <h1>What stays at the site, what reaches WatchLog and why.</h1>
    <p class="lead measure">WatchLog is designed around local video handling. Recorded video stays on the recorder while selected product data reaches the SaaS platform.</p>
  </div>
</section>

<section class="surface">
  <div class="wrap"><div class="prose">
    <h2>What stays at your site</h2>
    <ul>
      <li><strong>Recorded video.</strong> The current WatchLog architecture does not continuously upload recorded video to the cloud and does not provide remote archive browsing.</li>
      <li><strong>Recorder credentials.</strong> Recorder credentials remain on the site PC. On supported Windows installs, the recorder password is protected using machine-scoped Windows DPAPI rather than being stored as plaintext in the application configuration.</li>
      <li><strong>Local processing state.</strong> Temporary frames and tracking state used for on-site analysis are not treated as a cloud video archive.</li>
    </ul>

    <h2>What WatchLog may receive</h2>
    <ul>
      <li>Event records such as time, camera, event type and operational metadata.</li>
      <li>Selected event stills that are approved by the product flow.</li>
      <li>Configuration stills used when setting up lines, zones or camera-purpose rules.</li>
      <li>Configured Analytics Studio measurements and aggregates.</li>
      <li>Site and camera health signals, including when a site or camera last reported.</li>
      <li>Account, team, report-recipient and support information needed to operate the service.</li>
      <li>Billing profile, invoice, contract and payment-record information needed to administer commercial accounts.</li>
    </ul>

    <h2>What WatchLog does not do</h2>
    <p>WatchLog does not perform facial recognition. The current product does not provide live camera browsing, pan or zoom control, or remote recorded-video archive access.</p>

    <h2>People in event or configuration stills</h2>
    <p>Selected stills may contain identifiable people depending on the camera view. Customers are responsible for having the right to operate their cameras and use the service in their environment. WatchLog processes approved product data to provide the contracted service.</p>

    <h2>Retention</h2>
    <p>Incident-still retention is plan-dependent in the current product. Starter, Growth and quoted Enterprise tiers can use different retention periods. Account, commercial and audit records may need different retention because they support billing, support, security and legal obligations.</p>

    <h2>Customer access and tenant separation</h2>
    <p>Customer access is controlled through authenticated accounts and tenant-scoped authorization. Internal support access is designed to preserve platform-admin identity and audit context rather than silently sharing or replacing a customer login.</p>

    <h2>Payment information</h2>
    <p>WatchLog may keep billing profiles, invoices, contract references and payment records. If an external payment gateway is enabled, its own payment processing and privacy terms may also apply. The website should not claim a payment gateway is active until one is actually enabled.</p>

    <h2>Integrations and custom solutions</h2>
    <p>POS, Shopify, attendance, CRM and other integrations are customer-specific work unless and until a connector is productized. The data shared with an external system depends on the approved integration scope and permissions.</p>

    <h2>Questions or data requests</h2>
    <p>Use the <a href="<?php echo watchlog_url('contact'); ?>">Contact</a> page for privacy, account or data questions. Any deletion or export request is subject to account ownership, security checks and records that must be retained for legitimate commercial or legal reasons.</p>
  </div></div>
</section>
<?php get_footer(); ?>
