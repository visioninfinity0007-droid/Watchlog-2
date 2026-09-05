<?php
/* Pricing: verified plan numbers only. Enterprise and custom work remain quoted. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap center" style="max-width:54rem;margin-inline:auto">
    <nav class="crumbs" aria-label="Breadcrumb" style="justify-content:center"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Pricing</span></nav>
    <span class="eyebrow">SaaS pricing</span>
    <h1>Simple per-site pricing in PKR.</h1>
    <p class="lead measure" style="margin-inline:auto">Start with the current product for 14 days with no card. The standard tiers are monthly SaaS plans. Enterprise, pilots and custom integrations are discussed separately when the scope is not standard.</p>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="grid g3 price-grid">
      <div class="price-card">
        <h3>Starter</h3><p class="price"><span>PKR</span> 6,000<small>per site / month</small></p>
        <p class="price-for">For smaller sites that want daily visibility.</p>
        <ul class="ticks" style="margin:0 0 1.6rem">
          <li><?php echo watchlog_icon('camera',18); ?> Up to 8 cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 7 days of incident stills</li>
          <li><?php echo watchlog_icon('report',18); ?> Daily report on WhatsApp &amp; email</li>
          <li><?php echo watchlog_icon('people',18); ?> Unlimited team members</li>
        </ul>
        <a class="btn btn-secondary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card featured">
        <span class="price-tag">Most popular</span>
        <h3>Growth</h3><p class="price"><span>PKR</span> 12,000<small>per site / month</small></p>
        <p class="price-for">More cameras and a longer history of stills.</p>
        <ul class="ticks" style="margin:0 0 1.6rem">
          <li><?php echo watchlog_icon('camera',18); ?> Up to 24 cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 30 days of incident stills</li>
          <li><?php echo watchlog_icon('report',18); ?> Daily report on WhatsApp &amp; email</li>
          <li><?php echo watchlog_icon('chart',18); ?> Activity analytics &amp; Site Health</li>
        </ul>
        <a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card">
        <h3>Enterprise</h3><p class="price price-talk">Talk to us</p>
        <p class="price-for">Multi-site rollouts, pilots and commercial scopes outside the standard tiers.</p>
        <ul class="ticks" style="margin:0 0 1.6rem">
          <li><?php echo watchlog_icon('camera',18); ?> Unlimited cameras in the quoted plan</li>
          <li><?php echo watchlog_icon('clock',18); ?> Up to 90 days of incident stills</li>
          <li><?php echo watchlog_icon('sites',18); ?> Central view across many sites</li>
          <li><?php echo watchlog_icon('people',18); ?> Per-site recipients &amp; roles</li>
        </ul>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
      </div>
    </div>
    <p class="center" style="margin-top:28px;color:var(--slate-500)">Standard plan prices are per site and per month. Custom integrations, Control Room pilots and other implementation work can be quoted separately because the scope and acceptance criteria vary by customer.</p>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Billing &amp; payment</span><h2>What happens before and after payment.</h2></div>
    <div class="grid g3">
      <div class="card"><h3>14-day trial</h3><p>The current trial needs no card. Use it to assess the recorder and current product before a paid standard subscription.</p></div>
      <div class="card"><h3>Monthly subscription</h3><p>Standard SaaS billing is monthly per site, in advance. Cancellation stops future renewal after the already-paid period.</p></div>
      <div class="card"><h3>Payment method</h3><p>Payment options are confirmed on the invoice or checkout shown to the customer. Manual bank transfer may be used during rollout; an online payment gateway should only be presented once it is actually enabled.</p></div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Good to know</span><h2>Commercial terms without hidden assumptions.</h2></div>
    <div class="faq">
      <div class="faq-item"><button aria-expanded="false">Is there really no card for the trial? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Correct. The current 14-day trial needs no card.</div></div>
      <div class="faq-item"><button aria-expanded="false">What happens when the trial ends? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Reporting pauses until the customer has an active subscription. Existing account history is not represented as a new paid service until the account is active again.</div></div>
      <div class="faq-item"><button aria-expanded="false">Can I cancel a monthly plan? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">Yes. Under the current published terms, a standard monthly subscription continues through the already-paid month and then stops renewing.</div></div>
      <div class="faq-item"><button aria-expanded="false">Are events charged individually? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">The current Starter and Growth SaaS plans are not priced per incident. A future or custom transaction-based commercial model would need to be quoted and described separately rather than silently applied to these plans.</div></div>
      <div class="faq-item"><button aria-expanded="false">What is the refund policy? <?php echo watchlog_icon('chevron',20,'caret'); ?></button><div class="faq-a">The final refund rule still requires business-owner approval. The dedicated policy page states that openly rather than inventing a commitment. It must be finalized before merchant submission that requires a definitive refund rule.</div></div>
    </div>
    <p class="center" style="margin-top:28px"><a href="<?php echo watchlog_url('refund-cancellation-service-delivery'); ?>">Refund, Cancellation &amp; Service Delivery</a> · <a href="<?php echo watchlog_url('terms'); ?>">Terms</a> · <a href="<?php echo watchlog_url('faq'); ?>">FAQ</a></p>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Start with the standard product. Scope more only if you need it.</h2>
    <p class="lead measure">Fourteen days, no card.</p>
    <div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a></div>
  </div>
</section>
<?php get_footer(); ?>
