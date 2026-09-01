<?php
/* Privacy: what we hold, why, how long. Precise claims only. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:50rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Privacy</span></nav>
    <span class="eyebrow">Privacy</span>
    <h1>What we hold, why, and for how long.</h1>
    <p class="lead measure">WatchLog is built so the sensitive material never has to move. Here is exactly
      what stays at your site and what reaches us.</p>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="prose">
      <h2>What stays at your site</h2>
      <ul>
        <li><strong>Your recorded video.</strong> It stays on your recorder. We never receive it and cannot browse it.</li>
        <li><strong>Your recorder's username and password.</strong> Held in a file on your site PC; never sent to WatchLog.</li>
        <li><strong>Frames that didn't pass the on-site filter.</strong> Discarded on your machine and never uploaded.</li>
      </ul>

      <h2>What we receive</h2>
      <ul>
        <li>Event records: time, camera, and event type.</li>
        <li>One still image per event that passed the on-site filter.</li>
        <li>Health signals: when each site and camera was last heard from.</li>
        <li>Your account details and the addresses reports are sent to.</li>
      </ul>

      <h2>What we do not do</h2>
      <p>We do not have live access to your cameras, cannot pan or zoom them, and cannot browse your
        recorded footage. WatchLog does not perform facial recognition.</p>

      <h2>How long stills are kept</h2>
      <p>By your plan: 7, 30 or 90 days. After that they are deleted. Event records without images are
        kept for as long as your account is open.</p>

      <h2>Who can see it</h2>
      <p>Only people you have invited to your account. Data is separated per customer at the database
        level, and that separation is verified by an automated test before every release. Traffic to
        WatchLog is over HTTPS.</p>

      <h2>People in the images</h2>
      <p>Stills may contain identifiable people. You are the controller of that material; we process it
        on your behalf to produce your reports. If you need images removed, ask and we will delete them.</p>

      <h2>Your data if you leave</h2>
      <p>Export your event history before closing the account. After closure it is deleted within thirty days.</p>
    </div>
  </div>
</section>
<?php get_footer(); ?>
