<?php
/* Merchant-facing policy page. Refund rule remains explicit pending owner approval. */
if (!defined('ABSPATH')) { exit; }
get_header();
$billing_email = trim(get_option('watchlog_billing_email', getenv('WATCHLOG_BILLING_EMAIL') ?: watchlog_contact_email()));
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Refund, Cancellation &amp; Service Delivery</span></nav>
    <span class="eyebrow">Commercial policy</span>
    <h1>Refund, cancellation and digital-service delivery.</h1>
    <p class="lead measure">WatchLog is a digital SaaS and implementation service. No physical goods are shipped as part of the standard subscription.</p>
  </div>
</section>

<section class="surface">
  <div class="wrap"><div class="prose">
    <h2>Service delivery</h2>
    <p>Standard WatchLog access is delivered digitally through the customer portal and the WatchLog site software. The current offer includes a 14-day trial with no card. Recorder and camera compatibility still needs to be confirmed against the actual environment, and camera-based measurements may require site calibration.</p>

    <h2>Physical shipping</h2>
    <p>The standard WatchLog SaaS subscription does not include physical shipping. If a future customer order includes hardware or another physical item, its delivery terms must be stated separately in the applicable quotation or order before payment.</p>

    <h2>Subscription billing</h2>
    <p>Standard subscriptions are billed monthly per site, in advance. Starter and Growth pricing is shown in PKR on the Pricing page. Enterprise and custom implementation work may use a separate written quotation, invoice or contract.</p>

    <h2>Cancellation</h2>
    <p>You may cancel a standard monthly subscription. Under the current published terms, service and reporting continue until the end of the already-paid monthly period, then the subscription stops renewing. A lapsed trial or subscription pauses reporting.</p>

    <h2>Refunds</h2>
    <div class="note-card">
      <p><strong>Final refund rule awaiting business-owner approval.</strong> WatchLog will not invent a refund commitment before the legal business owner approves it. The final rule must be published here before payment-gateway activation or merchant submission that requires a definitive refund policy.</p>
    </div>
    <p>Until that approval is published, any refund question must be resolved against the written invoice, quotation or contract for the specific order and any applicable law. This page deliberately does not promise an automatic refund or a blanket no-refund rule without owner approval.</p>

    <h2>Custom implementation and pilot work</h2>
    <p>Custom integrations, industry-specific builds and pilot work may have different payment milestones, acceptance criteria, support obligations and cancellation terms. Those terms should be written in the applicable proposal or contract before work begins.</p>

    <h2>How to request cancellation or ask a billing question</h2>
    <p>Use the Contact page and identify the customer account and site.<?php if ($billing_email !== '') : ?> Billing enquiries may also be sent to <strong><?php echo esc_html($billing_email); ?></strong>.<?php endif; ?></p>

    <h2>Related policies</h2>
    <p><a href="<?php echo watchlog_url('pricing'); ?>">Pricing</a> · <a href="<?php echo watchlog_url('terms'); ?>">Terms</a> · <a href="<?php echo watchlog_url('privacy'); ?>">Privacy</a> · <a href="<?php echo watchlog_url('contact'); ?>">Contact</a></p>
  </div></div>
</section>
<?php get_footer(); ?>
