<?php
/**
 * Pricing.
 *
 * A pricing page's job is to remove doubt, not to look clever. So:
 * three plans side by side, a comparison table for the person who wants
 * detail, and an FAQ answering the questions that otherwise become
 * emails — what happens after the trial, whether the price changes, what
 * it costs to leave.
 *
 * The "what could cost you more later" answer stays in, even though no
 * pricing page volunteers that. This is sold to people who have been
 * mis-sold before; naming the thing that could surprise them is what
 * makes the rest of the page believable.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = watchlog_signup_url();
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>Pricing
    </nav>
    <h1>Per site, in rupees. No setup fee.</h1>
    <p class="lede">Nothing is metered. The only thing that changes your bill is
      adding a site or moving up a camera tier.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="plan-grid">

      <div class="card plan">
        <h3>Starter</h3>
        <div class="price">PKR 6,000<small> / site / month</small></div>
        <p class="tiny">For one location with a handful of cameras.</p>
        <ul class="plan-features">
          <li>Up to 8 cameras</li>
          <li>7 days of incident stills</li>
          <li>Daily WhatsApp report</li>
          <li>Site health and fault alerts</li>
          <li>Unlimited team members</li>
        </ul>
        <a class="btn btn-ghost" href="<?php echo esc_url($portal); ?>">Start free</a>
      </div>

      <div class="card plan featured">
        <span class="tag">Most sites</span>
        <h3>Growth</h3>
        <div class="price">PKR 12,000<small> / site / month</small></div>
        <p class="tiny">The usual choice for a warehouse or a branch.</p>
        <ul class="plan-features">
          <li>Up to 24 cameras</li>
          <li>30 days of incident stills</li>
          <li>Everything in Starter</li>
          <li>Analytics and trends</li>
          <li>Per-site report recipients</li>
        </ul>
        <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start free</a>
      </div>

      <div class="card plan">
        <h3>Enterprise</h3>
        <div class="price">Talk to us</div>
        <p class="tiny">Several locations, or more than 24 cameras on one.</p>
        <ul class="plan-features">
          <li>Unlimited cameras</li>
          <li>90 days of incident stills</li>
          <li>Everything in Growth</li>
          <li>Multi-site rollout support</li>
          <li>Priority response</li>
        </ul>
        <a class="btn btn-ghost" href="/contact/">Contact sales</a>
      </div>
    </div>

    <div class="note plan-foot">
      <?php echo watchlog_icon('check', 20); ?>
      <p><strong>Fourteen days free, no card.</strong> If WatchLog does not work
        with your recorder you will know within ten minutes of installing —
        not after you have paid.</p>
    </div>
  </div>
</section>

<section class="alt">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Compare</div>
      <h2>What is in each plan</h2>
    </div>
    <table class="compare">
      <tr><th>&nbsp;</th><th>Starter</th><th>Growth</th><th>Enterprise</th></tr>
      <tr><td>Cameras per site</td><td>8</td><td>24</td><td>Unlimited</td></tr>
      <tr><td>Incident stills kept</td><td>7 days</td><td>30 days</td><td>90 days</td></tr>
      <tr><td>Daily WhatsApp report</td><td class="yes">Yes</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>Email report</td><td class="yes">Yes</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>Site health and fault alerts</td><td class="yes">Yes</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>False-alarm filtering on site</td><td class="yes">Yes</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>Analytics and trends</td><td class="no">—</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>Per-site report recipients</td><td class="no">—</td><td class="yes">Yes</td><td class="yes">Yes</td></tr>
      <tr><td>Team members</td><td>Unlimited</td><td>Unlimited</td><td>Unlimited</td></tr>
      <tr><td>Multi-site rollout support</td><td class="no">—</td><td class="no">—</td><td class="yes">Yes</td></tr>
    </table>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Questions</div>
      <h2>The things people ask before buying</h2>
    </div>

    <div class="faq">
      <details open>
        <summary>What happens when the trial ends?</summary>
        <p>Reporting stops and we email you. Nothing is deleted, and nothing is
          charged — there is no card on file to charge. Add one when you are
          ready and it picks up where it left off.</p>
      </details>
      <details>
        <summary>Could the price change once you see how much we use?</summary>
        <p>No. It is per site, not per event, per image or per gigabyte. The
          only things that move it are adding a site or going over your camera
          tier, and we would tell you before that happened.</p>
      </details>
      <details>
        <summary>What if our recorder turns out not to be supported?</summary>
        <p>You will find out in the first ten minutes, during the free trial,
          before any money changes hands. The setup program tells you in plain
          language what it found and whether it can talk to it.</p>
      </details>
      <details>
        <summary>Do we need to buy anything?</summary>
        <p>No cameras, no recorder, no licence dongle. You need a Windows PC at
          the site that stays switched on, which almost every business already
          has.</p>
      </details>
      <details>
        <summary>Can we cancel?</summary>
        <p>Any time. It runs to the end of the month you have paid for and then
          stops. Export your history first — after the account closes it is
          deleted within thirty days.</p>
      </details>
      <details>
        <summary>Is there a discount for several sites?</summary>
        <p>Yes, from four sites upward. Tell us how many and where, and we will
          give you a number rather than a range.</p>
      </details>
    </div>
  </div>
</section>

<section class="cta-band">
  <div class="wrap center">
    <h2>Try it on one site first</h2>
    <p class="lede center-lede">Pick your busiest location. Fourteen days, no
      card, no call required.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/setup/">Check the requirements</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
