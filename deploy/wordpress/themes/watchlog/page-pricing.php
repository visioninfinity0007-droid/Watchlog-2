<?php
/* Pricing — verified numbers only: 6,000 / 12,000 / Talk to us. 14-day trial. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap center" style="max-width:52rem;margin-inline:auto">
    <nav class="crumbs" aria-label="Breadcrumb" style="justify-content:center"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Pricing</span></nav>
    <span class="eyebrow">Pricing</span>
    <h1>Priced per site, in rupees.</h1>
    <p class="lead measure" style="margin-inline:auto">No setup fee, no hardware to buy, no contract.
      Start free for 14 days — no card. If WatchLog doesn't work with your recorder, you'll know within
      minutes of installing.</p>
  </div>
</section>

<section class="light">
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
          <li><?php echo watchlog_icon('chart',18); ?> Activity analytics &amp; site health</li>
        </ul>
        <a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card">
        <h3>Enterprise</h3><p class="price price-talk">Talk to us</p>
        <p class="price-for">Multi-site rollouts with unlimited cameras.</p>
        <ul class="ticks" style="margin:0 0 1.6rem">
          <li><?php echo watchlog_icon('camera',18); ?> Unlimited cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 90 days of incident stills</li>
          <li><?php echo watchlog_icon('sites',18); ?> Central view across many sites</li>
          <li><?php echo watchlog_icon('people',18); ?> Per-site recipients &amp; roles</li>
        </ul>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
      </div>
    </div>
    <p class="center" style="margin-top:28px;color:var(--slate-500)">Every plan includes the daily report,
      incident stills, site health, analytics and unlimited team members. Priced per site — the only thing
      that changes the number is adding a site or moving up a camera tier.</p>
  </div>
</section>

<section class="cloud">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Good to know</span><h2>The honest small print.</h2></div>
    <div class="faq">
      <div class="faq-item"><button aria-expanded="false">Is there really no card for the trial? <?php echo watchlog_icon('chevron',20,'caret'); ?></button>
        <div class="faq-a">Correct — the 14-day trial needs no card. It exists so you can prove WatchLog works with your recorder before paying, not after.</div></div>
      <div class="faq-item"><button aria-expanded="false">What happens when the trial ends? <?php echo watchlog_icon('chevron',20,'caret'); ?></button>
        <div class="faq-a">Reporting pauses until you subscribe. Your recorded events and history are kept — nothing is deleted — so paying later picks up where you left off.</div></div>
      <div class="faq-item"><button aria-expanded="false">Can I cancel any time? <?php echo watchlog_icon('chevron',20,'caret'); ?></button>
        <div class="faq-a">Yes. Billing is monthly, per site, in advance. Cancel and reporting continues to the end of the paid month.</div></div>
      <div class="faq-item"><button aria-expanded="false">Is anything metered or charged per event? <?php echo watchlog_icon('chevron',20,'caret'); ?></button>
        <div class="faq-a">No. The price is per site. It doesn't change with how many events or reports you generate.</div></div>
      <div class="faq-item"><button aria-expanded="false">Do I need to buy hardware? <?php echo watchlog_icon('chevron',20,'caret'); ?></button>
        <div class="faq-a">No. WatchLog uses the recorder and cameras you already own, plus an ordinary Windows PC at the site that stays switched on.</div></div>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Start free. Decide later.</h2>
    <p class="lead measure">Fourteen days, no card, no obligation.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
