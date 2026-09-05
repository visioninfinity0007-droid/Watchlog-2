<?php
/* FAQ: public answers stay within current product and merchant-policy evidence. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>FAQ</span></nav>
    <span class="eyebrow">FAQ</span>
    <h1>Questions about the product, setup, billing and data.</h1>
    <p class="lead measure">The short version: WatchLog adds a local intelligence layer to existing CCTV, sends selected business events and stills to the SaaS portal, and keeps roadmap or custom work clearly labelled.</p>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="faq">
      <div class="faq-item"><button aria-expanded="false">What is WatchLog? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">WatchLog is a Video Analytics &amp; CCTV Intelligence SaaS platform for businesses that already have cameras and a recorder. It combines incident intelligence, site health, configured analytics and reporting.</div></div>
      <div class="faq-item"><button aria-expanded="false">Do I have to replace my cameras? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Not by default. WatchLog is designed to assess and work with existing recorder environments. Compatibility is confirmed against the actual recorder and camera views during setup or pilot acceptance.</div></div>
      <div class="faq-item"><button aria-expanded="false">Does WatchLog upload all of my video? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">No. Recorded video stays on your recorder. The current architecture uses on-site processing and sends approved event data, health signals, configuration stills and selected event stills needed by the product.</div></div>
      <div class="faq-item"><button aria-expanded="false">Does WatchLog use facial recognition? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">No. Current people-flow and dwell measurements are anonymous operational analytics. Attendance-system integration is a separate custom-solution direction and should not be confused with facial recognition.</div></div>
      <div class="faq-item"><button aria-expanded="false">What analytics are available today? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Configured people flow, vehicle flow, boundary activity, dwell or time in zone, after-hours activity and checkout-zone activity are part of Analytics Studio. Measurement quality depends on camera placement, lighting and site conditions.</div></div>
      <div class="faq-item"><button aria-expanded="false">Does checkout-zone activity mean transaction counting? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">No. It measures activity or occupancy in a configured area. It is not exact transactions, sales, revenue or till reconciliation.</div></div>
      <div class="faq-item"><button aria-expanded="false">Is Control Room available now? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">No. Control Room is a pilot or coming-soon direction for central multi-site operations. The current product does not provide a live video wall.</div></div>
      <div class="faq-item"><button aria-expanded="false">Is fire and smoke detection available? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Not as a standard production detector today. Fire and smoke detection are roadmap research and pilot areas that require validation before they can be represented as available.</div></div>
      <div class="faq-item"><button aria-expanded="false">Can WatchLog integrate with POS, Shopify, attendance or CRM systems? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">These can be scoped as Custom Solution work. WatchLog does not currently advertise a catalogue of finished plug-and-play connectors for those systems.</div></div>
      <div class="faq-item"><button aria-expanded="false">How is WatchLog priced? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Starter is PKR 6,000 per site per month and Growth is PKR 12,000 per site per month. Enterprise and custom implementations are quoted separately. See the Pricing page for the current plan details.</div></div>
      <div class="faq-item"><button aria-expanded="false">Is there a free trial? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">The current SaaS offer includes a 14-day trial with no card. Reporting pauses when a trial or subscription is no longer active.</div></div>
      <div class="faq-item"><button aria-expanded="false">How do cancellation and service delivery work? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">WatchLog is a digital SaaS service, so no physical shipping applies. Monthly cancellation and service-delivery details are published on the Refund, Cancellation &amp; Service Delivery page. The final refund rule is still awaiting business-owner approval and is not invented on this site.</div></div>
      <div class="faq-item"><button aria-expanded="false">How do I get support? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Use the Contact page for recorder compatibility, rollout planning, current-customer support, after-sales help, partnerships or custom-solution discovery.</div></div>
    </div>
  </div>
</section>

<section class="dark field cta-band"><div class="wrap center"><h2>Still deciding whether a camera view can answer your question?</h2><p class="lead measure">Start with the site, the recorder and the operational outcome you need.</p><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Talk to WatchLog</a></div></section>
<?php get_footer(); ?>
