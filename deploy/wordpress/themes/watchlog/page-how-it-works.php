<?php
/**
 * How it works.
 *
 * The mechanics, including the unglamorous parts. The reader arriving
 * here is usually the technical person being asked to approve it, and
 * their real question is "what does this open up on my network?".
 *
 * So the outward-only diagram is the centrepiece, and the answer is
 * given before the feature list rather than after.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = watchlog_signup_url();
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>How it works
    </nav>
    <h1>The mechanics, including the unglamorous parts</h1>
    <p class="lede">Nothing connects in. No port forwarding, no VPN, no change
      to your firewall.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="flow">
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('recorder'); ?></span>
        <h3>Your recorder</h3>
        <p>Stays where it is, doing what it already does. We change nothing
          about it.</p>
      </div>
      <div class="flow-arrow"><?php echo watchlog_icon('arrow-out', 22); ?></div>
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('pc'); ?></span>
        <h3>A PC on your network</h3>
        <p>Reads the event log, takes a still, discards the false alarms
          locally.</p>
      </div>
      <div class="flow-arrow"><?php echo watchlog_icon('arrow-out', 22); ?></div>
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('cloud'); ?></span>
        <h3>Your report</h3>
        <p>WhatsApp each morning, and the portal whenever you want to look.</p>
      </div>
    </div>

    <?php if (watchlog_has_img('site-pc')) : ?>
      <figure class="shot wide">
        <?php echo watchlog_img('site-pc',
          'An ordinary older desktop PC under a desk in a small office, with a network cable plugged in.',
          1600, 1000); ?>
        <figcaption>This is the whole hardware requirement. It does not need
          to be new, or fast, or dedicated &mdash; only switched on.</figcaption>
      </figure>
    <?php endif; ?>

    <div class="note">
      <?php echo watchlog_icon('lock', 20); ?>
      <p><strong>The arrows only point one way.</strong> The site program opens a
        connection outward. There is no inbound route, no listening port, and
        nothing of yours placed on the internet.</p>
    </div>
  </div>
</section>

<section class="alt">
  <div class="wrap">
    <div class="prose-grid">
      <nav class="toc" aria-label="On this page">
        <div class="toc-title">On this page</div>
        <ul>
          <li><a href="#site">The program at your site</a></li>
          <li><a href="#outward">Why nothing connects in</a></li>
          <li><a href="#log">Reading the event log</a></li>
          <li><a href="#filter">Discarding false alarms</a></li>
          <li><a href="#report">What gets sent, and when</a></li>
          <li><a href="#offline">If the internet drops</a></li>
          <li><a href="#limits">What we cannot do</a></li>
        </ul>
      </nav>

      <div class="prose-body">
        <h2 id="site">The program at your site</h2>
        <p>It installs on any Windows PC on the same network as your recorder —
          an office machine, the reception PC, anything that stays switched on.
          It uses almost no resources and has no window; it runs quietly in the
          background and starts again by itself after a reboot.</p>

        <h2 id="outward">Why nothing connects in</h2>
        <p>Most remote-viewing setups require port forwarding, which puts your
          recorder's login page on the public internet. Recorders are scanned
          for constantly, and their default passwords are well known. We refuse
          to work that way.</p>
        <p>Instead the site program opens a connection <em>out</em> to WatchLog,
          the same way a browser does. Your firewall needs no change, your
          recorder gets no public address, and its credentials never leave the
          building.</p>

        <h2 id="log">Reading the event log</h2>
        <p>Recorders already detect motion and write it to a log. WatchLog reads
          that log rather than analysing video, which is why it works on
          hardware you already own and does not need a powerful machine.</p>
        <p>For each event it also asks the camera for a single still image at
          that moment.</p>

        <h2 id="filter">Discarding false alarms</h2>
        <p>Most motion is not an incident. Rain, headlights sweeping a wall, a
          cat, a flag, the infrared lamp cutting in at dusk — all motion, none
          worth a line in your morning report.</p>
        <p>Each still is checked <strong>on your own PC</strong> for a person, a
          car or a motorcycle. If there is none, the event is dropped and never
          leaves the building. Without this a single recorder produces hundreds
          of events a night and the report becomes unreadable — which is the
          exact failure this product exists to avoid.</p>
        <p>If that check cannot run for any reason, events are kept rather than
          dropped. A missed incident is invisible; an extra one is only noise.</p>

        <h2 id="report">What gets sent, and when</h2>
        <p>Only events that passed the filter are uploaded, each with its still.
          They appear in the portal within a minute or so. The summary is sent
          each morning, covering yesterday in your site's own timezone.</p>

        <h2 id="offline">If the internet drops</h2>
        <p>Events are stored on the site PC and sent when the connection
          returns. Nothing is lost to a bad line. If the site stays quiet longer
          than expected, you are told — a silent site and a safe site look
          identical otherwise.</p>

        <h2 id="limits">What we cannot do</h2>
        <p>We receive event records and the stills attached to them. We have no
          live access to your cameras, cannot pan, tilt or zoom them, and cannot
          browse your recorded footage. There is no mechanism for it, not merely
          a policy against it.</p>
      </div>
    </div>
  </div>
</section>

<section class="cta-band">
  <div class="wrap center">
    <h2>Ten minutes, on one site</h2>
    <p class="lede center-lede">If it cannot reach your recorder it will tell
      you why, in plain language.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/setup/">What you need first</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
