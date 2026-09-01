<?php
/* Terms — plain-terms commitments. WatchLog reports; it does not guard. */
if (!defined('ABSPATH')) { exit; }
get_header();
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:50rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Terms</span></nav>
    <span class="eyebrow">Terms</span>
    <h1>The commitments on both sides, in plain terms.</h1>
    <p class="lead measure">WatchLog is a reporting service. It works alongside your security team — it
      doesn't replace guarding, monitoring or a response.</p>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="prose">
      <h2>What WatchLog does</h2>
      <p>It reads the event log of a recorder you own, filters out false alarms on site, and reports what
        is left, each morning. It is a reporting service.</p>

      <h2>What it does not do</h2>
      <p>It does not prevent incidents, does not monitor live, does not dispatch a response, and is not a
        substitute for guarding, alarms or insurance. It gives your guards, supervisors and site managers
        better visibility — it does not replace them. No reporting service can stop something happening.</p>

      <h2>Availability</h2>
      <p>If a site's internet drops, events are stored locally and sent when it returns. If your recorder
        is off, powered down or unreachable, there is nothing to report — and we will tell you the site has
        gone quiet.</p>

      <h2>Your responsibilities</h2>
      <ul>
        <li>Keeping the site PC switched on and connected.</li>
        <li>Having the right to place cameras where they are, and to process the images they capture.</li>
        <li>Keeping your account credentials to yourself.</li>
      </ul>

      <h2>Billing</h2>
      <p>Monthly per site, in advance. The trial is fourteen days and needs no card. Cancel any time and
        reporting continues to the end of the paid month. When a trial or subscription lapses, reporting
        pauses — your recorded events and history are kept.</p>

      <h2>Your data if you leave</h2>
      <p>Export your event history before you close the account. After closure it is deleted within thirty days.</p>
    </div>
  </div>
</section>
<?php get_footer(); ?>
