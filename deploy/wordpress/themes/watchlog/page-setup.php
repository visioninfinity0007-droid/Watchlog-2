<?php
/**
 * What you need.
 *
 * This page exists because of one rule in the site map: the requirements
 * are stated BEFORE sign-up, not discovered during it. A customer who
 * signs up and then learns they need a PC at the site is a refund and a
 * bad review.
 *
 * So the unsupported-hardware warning is given the same visual weight as
 * the supported list, rather than being a footnote. Most SaaS hides
 * this. Hiding it converts a few more trials and loses them all again at
 * the first phone call.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = get_option('watchlog_portal_url', '#');
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>What you need
    </nav>
    <h1>What you need, before you sign up</h1>
    <p class="lede">Four things. If you have all four, setup takes about ten
      minutes. If you do not, we would rather you knew now.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="grid g2" style="gap:40px">

      <div>
        <h2>You will need</h2>
        <div class="grid" style="gap:16px;margin-top:22px">
          <div class="card">
            <span class="ico-badge"><?php echo watchlog_icon('recorder'); ?></span>
            <h3>A supported recorder</h3>
            <p>Hikvision, HiLook, Dahua, Imou, CP Plus, Uniview, Tiandy, or most
              ONVIF-conformant units.</p>
          </div>
          <div class="card">
            <span class="ico-badge"><?php echo watchlog_icon('pc'); ?></span>
            <h3>A Windows PC at the site</h3>
            <p>On the same network as the recorder, left switched on. Any
              ordinary office machine. It does not need to be new.</p>
          </div>
          <div class="card">
            <span class="ico-badge"><?php echo watchlog_icon('lock'); ?></span>
            <h3>The recorder's login</h3>
            <p>Admin username and password. Typed in at the site, stored on that
              PC, and never sent to us.</p>
          </div>
          <div class="card">
            <span class="ico-badge"><?php echo watchlog_icon('cloud'); ?></span>
            <h3>An ordinary internet connection</h3>
            <p>No fixed IP, no port forwarding, no firewall changes. The
              connection only goes outward.</p>
          </div>
        </div>
      </div>

      <div>
        <?php if (watchlog_has_img('recorder-label')) : ?>
          <figure class="shot" style="margin-bottom:22px">
            <?php echo watchlog_img('recorder-label',
              'A hand holding a small recorder, showing the printed barcode label on its underside.',
              1400, 1000); ?>
            <figcaption>This label. A photo of it is usually enough for us to
              tell you yes or no.</figcaption>
          </figure>
        <?php endif; ?>

        <aside class="callout">
          <span class="ico-badge bad"><?php echo watchlog_icon('alert'); ?></span>
          <h3>What is not supported</h3>
          <p>The cheapest unbranded recorders — typically built on Xiongmai or
            Hisilicon boards and sold without a brand name — do not speak a
            standard protocol reliably enough for us to support them.</p>
          <p><strong>Not sure what you have?</strong> Send us a photo of the
            label on the underside of the box. We will tell you yes or no,
            usually the same day, before you spend anything.</p>
          <p class="tiny">We would rather lose the sale here than take your
            money and disappoint you in week two.</p>
        </aside>

        <div class="note" style="margin-top:22px">
          <?php echo watchlog_icon('clock', 20); ?>
          <p><strong>About ten minutes.</strong> Most of it is finding the
            recorder's password. If the setup program cannot reach your
            recorder, it says why in plain language rather than failing
            silently.</p>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="alt">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Installing</div>
      <h2>Step by step</h2>
      <p class="lede">Nothing here needs an engineer. If you can install a
        printer, you can do this.</p>
    </div>

    <div class="grid g2" style="gap:40px">
      <div class="prose-body">
        <ol>
          <li><strong>Create an account and add your site.</strong> You will be
            given a one-time enrollment code.</li>
          <li><strong>Download the WatchLog agent</strong> onto the site PC.</li>
          <li><strong>Run it.</strong> A setup wizard asks for the recorder's
            address and login. If you do not know the address, it searches your
            network and offers what it finds.</li>
          <li><strong>Paste the enrollment code.</strong> The site appears in
            your portal within a minute.</li>
          <li><strong>Add who gets the report,</strong> and on which channel —
            WhatsApp, email, or both.</li>
        </ol>
      </div>

      <div class="card">
        <h3>What the wizard does for you</h3>
        <ul class="checklist" style="margin-top:14px">
          <li><?php echo watchlog_icon('check', 19); ?><span>Scans your local network for recorders</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Works out which make it is and which protocol to use</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Tests the login before saving it</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Lists the cameras it can see, so you can check nothing is missing</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Tells you plainly what is wrong if it cannot connect</span></li>
        </ul>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Security</div>
      <h2>What leaves your building, and what does not</h2>
    </div>
    <div class="grid g2">
      <div class="card">
        <h3>Stays at your site</h3>
        <ul class="checklist" style="margin-top:14px">
          <li><?php echo watchlog_icon('check', 19); ?><span>Your recorded video — we never receive it</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Your recorder's username and password</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Frames that did not pass the false-alarm filter</span></li>
        </ul>
      </div>
      <div class="card">
        <h3>Sent to us</h3>
        <ul class="checklist" style="margin-top:14px">
          <li><?php echo watchlog_icon('check', 19); ?><span>Event records: time, camera, type</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>One still per event that passed the filter</span></li>
          <li><?php echo watchlog_icon('check', 19); ?><span>Health signals: when each camera was last heard from</span></li>
        </ul>
      </div>
    </div>
    <div class="note">
      <?php echo watchlog_icon('lock', 20); ?>
      <p>We cannot view your cameras live, cannot pan or zoom them, and cannot
        browse your recorded footage. The connection only goes one way, and
        there is no route back in.</p>
    </div>
  </div>
</section>

<section class="cta-band">
  <div class="wrap center">
    <h2>Have all four? It takes ten minutes</h2>
    <p class="lede center-lede">And if your recorder turns out not to be
      supported, you will know before you pay anything.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/contact/">Ask about our recorder</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
