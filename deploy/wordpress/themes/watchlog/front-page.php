<?php
/**
 * Home.
 *
 * Sections follow 03_Design/SITE_MAP.md in order, each with one job,
 * each handing to the next. Two choices in here are unusual and both are
 * deliberate:
 *
 *   - Section 6 says plainly what WatchLog is NOT. Most SaaS sites avoid
 *     this. Ours is sold to people who have been mis-sold CCTV before,
 *     and saying "not a guard service, not live monitoring" up front
 *     costs a few leads that were never going to convert and earns trust
 *     from the ones that will.
 *   - The compatibility section states the EXCLUSION in the open rather
 *     than the small print. A customer who signs up and then finds their
 *     unbranded recorder is unsupported is a refund and a bad review.
 *
 * The hero shows a real report rather than stock photography, per
 * BRAND_GUIDELINES: evidence over illustration.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = get_option('watchlog_portal_url', '#');
?>

<!-- 1. Hero --------------------------------------------------------- -->
<section class="hero">
  <div class="wrap grid g2" style="align-items:center;gap:56px">
    <div>
      <h1>Your cameras already see everything. WatchLog tells you what they saw.</h1>
      <p class="lede">Works with the Hikvision and Dahua recorders you already
        own. No new cameras, no rewiring, no monthly guard.</p>
      <div class="hero-actions">
        <a class="btn btn-primary" href="#report">See a sample report</a>
        <a class="btn btn-ghost" href="/how-it-works/">How it works</a>
      </div>
    </div>

    <div class="report" id="report">
      <div class="r-head">WatchLog — Karachi Head Office</div>
      <div class="r-date">Friday 28 August</div>
      <div class="r-line"><span class="tabular">14 events</span></div>
      <div class="r-line"><span class="pill pill-warn">2 after midnight</span>
        <span>Loading bay</span></div>
      <div class="r-line"><span class="pill pill-bad">1 fault</span>
        <span>Camera 3 silent for 26 hours</span></div>
      <div class="r-line"><span class="pill pill-ok">6 cameras healthy</span></div>
      <div class="r-line" style="margin-top:14px;color:var(--color-muted-dark);font-size:.9rem">
        Busiest: Main Gate (9). First 18:42, last 03:11.
      </div>
    </div>
  </div>
</section>

<!-- 2. The problem -------------------------------------------------- -->
<section class="alt">
  <div class="wrap">
    <div class="eyebrow">The problem</div>
    <h2 style="max-width:20ch">Your cameras record. Nobody watches.</h2>
    <p class="lede">Footage only gets opened after something has already gone
      wrong. By then it is evidence, not security.</p>
  </div>
</section>

<!-- 3. What arrives ------------------------------------------------- -->
<section>
  <div class="wrap">
    <div class="eyebrow">What arrives</div>
    <h2>A summary every morning, read in under a minute</h2>
    <p class="lede">Every incident is logged with a still image at the moment it
      happened. Nothing needs to be watched live.</p>
    <div class="grid g3" style="margin-top:34px">
      <div class="card">
        <h3>What happened</h3>
        <p>How many events, on which cameras, and how many were after hours.</p>
      </div>
      <div class="card">
        <h3>What it looked like</h3>
        <p>A still from the moment of each incident, so you can judge it without
          opening the recorder.</p>
      </div>
      <div class="card">
        <h3>What stopped working</h3>
        <p>A camera that has gone silent is reported as a fault. That is the
          failure nobody notices for weeks.</p>
      </div>
    </div>
  </div>
</section>

<!-- 4. How it works ------------------------------------------------- -->
<section class="alt">
  <div class="wrap">
    <div class="eyebrow">How it works</div>
    <h2>Three steps, about ten minutes</h2>
    <div class="grid g3" style="margin-top:34px">
      <div class="card">
        <div class="step-n">1</div>
        <h3>Install a small program</h3>
        <p>On any Windows PC at your site that stays switched on.</p>
      </div>
      <div class="card">
        <div class="step-n">2</div>
        <h3>It finds your recorder</h3>
        <p>It scans your own network, connects with credentials you type in, and
          nothing is exposed to the internet.</p>
      </div>
      <div class="card">
        <div class="step-n">3</div>
        <h3>Reports arrive</h3>
        <p>On WhatsApp every morning, and in the portal whenever you want to look.</p>
      </div>
    </div>
    <p style="margin-top:26px">
      <a href="/setup/">The full requirements, before you sign up →</a>
    </p>
  </div>
</section>

<!-- 5. Works with what you have ------------------------------------- -->
<section>
  <div class="wrap">
    <div class="eyebrow">Compatibility</div>
    <h2>Works with the recorder you already have</h2>
    <ul class="chips">
      <li>Hikvision</li><li>HiLook</li><li>Dahua</li><li>Imou</li>
      <li>CP Plus</li><li>Uniview</li><li>Tiandy</li><li>Most ONVIF recorders</li>
    </ul>
    <p class="lede" style="margin-top:24px">
      <strong>What is not supported:</strong> the cheapest unbranded recorders,
      the ones built on Xiongmai or Hisilicon boards sold without a brand name.
      They do not speak a standard protocol reliably. We would rather tell you
      here than after you have paid.
    </p>
  </div>
</section>

<!-- 6. What it is not ----------------------------------------------- -->
<section class="dark">
  <div class="wrap">
    <div class="eyebrow">Being straight with you</div>
    <h2>What WatchLog is not</h2>
    <ul class="notlist" style="margin-top:26px;max-width:70ch">
      <li><b>Not a guard service.</b>
        <span>Nobody is watching your cameras live. It reads what the recorder
          already logged and tells you.</span></li>
      <li><b>Not new cameras.</b>
        <span>It uses the ones you have. If your cameras cannot see the gate
          today, WatchLog will not either.</span></li>
      <li><b>Not live monitoring.</b>
        <span>Incidents are reported as they are logged and summarised each
          morning. It is not an alarm response service.</span></li>
      <li><b>Not a way to prevent anything.</b>
        <span>It observes and reports. Prevention is what the guards, gates and
          lighting are for.</span></li>
    </ul>
  </div>
</section>

<!-- 7. Close --------------------------------------------------------- -->
<section class="on-dark" style="background:var(--color-ink)">
  <div class="wrap" style="text-align:center">
    <h2 style="color:#fff">Find out what your cameras have been seeing</h2>
    <p class="lede" style="margin:0 auto 28px;color:var(--color-muted-dark)">
      Fourteen days, no card. If it does not work with your recorder you will
      know within ten minutes.</p>
    <div class="hero-actions" style="justify-content:center">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/contact/">Talk to us first</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
