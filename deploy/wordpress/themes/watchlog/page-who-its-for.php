<?php
/**
 * Who it's for.
 *
 * Alternating rows so the eye has a rhythm, and each segment leads with
 * the ONE number that segment actually reads first. A warehouse manager
 * and a school administrator do not care about the same line of the
 * report, and saying so is what makes each one feel described rather
 * than marketed at.
 *
 * The "who it is not for" block at the end is deliberate. Telling the
 * wrong customer to go elsewhere costs a lead and saves a refund.
 *
 * seg-media falls back to an icon until the photography in
 * 03_Design/IMAGE_PROMPTS.md exists, so the layout is never broken by a
 * missing file.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = watchlog_signup_url();

$segments = [
  ['warehouse', 'sites', 'Warehouses and yards',
   'Large perimeters, few people after hours, and a loading bay that matters. The after-hours count is the number that gets read first.',
   ['Perimeter', 'Loading bay', 'After hours']],
  ['retail', 'people', 'Retail chains',
   'One view across every branch, and a per-branch report to the person who runs it. Head office sees the pattern; the branch sees its own.',
   ['Multi-site', 'Per-branch reports', 'Trends']],
  ['school', 'clock', 'Schools and campuses',
   'Quiet at night by design, so anything after hours is worth a look. Gate and boundary cameras carry most of the value.',
   ['Gates', 'Boundary', 'Out of hours']],
  ['factory', 'alert', 'Factories',
   'Shift changes, vehicle movement, equipment areas. Camera faults matter as much as incidents here — a blind spot on a production floor is a safety issue.',
   ['Shift change', 'Vehicles', 'Camera faults']],
  ['office', 'report', 'Offices',
   'Small camera counts. The value is mostly knowing nothing happened, and being told the same day when a camera stops working.',
   ['Small sites', 'Daily summary', 'Fault alerts']],
];
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>Who it's for
    </nav>
    <h1>Businesses that bought CCTV and never looked at it again</h1>
    <p class="lede">Every one of these already has cameras. None of them has
      anyone reading what those cameras saw.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <?php foreach ($segments as [$slug, $icon, $title, $body, $tags]) :
      $img = get_template_directory() . "/img/seg-$slug.jpg"; ?>
      <div class="seg">
        <div>
          <h3><?php echo esc_html($title); ?></h3>
          <p><?php echo esc_html($body); ?></p>
          <div class="tag-row">
            <?php foreach ($tags as $t) : ?><span><?php echo esc_html($t); ?></span><?php endforeach; ?>
          </div>
        </div>
        <div class="seg-media">
          <?php if (file_exists($img)) : ?>
            <img src="<?php echo esc_url(get_template_directory_uri() . "/img/seg-$slug.jpg"); ?>"
                 alt="" loading="lazy" width="1200" height="800">
          <?php else : echo watchlog_icon($icon, 56); endif; ?>
        </div>
      </div>
    <?php endforeach; ?>
  </div>
</section>

<section class="dark">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Being straight with you</div>
      <h2>Who it is not for</h2>
    </div>
    <div class="grid g2 nots">
      <div class="not"><?php echo watchlog_icon('clock', 22); ?>
        <div><b>You need a live response.</b>
          <span>If somebody must be watching and reacting within minutes, you
            need a monitoring contract and a response team, not a daily
            report.</span></div></div>
      <div class="not"><?php echo watchlog_icon('camera', 22); ?>
        <div><b>Your cameras cannot see what matters.</b>
          <span>WatchLog reads what your cameras already capture. If the gate is
            out of frame today, it will still be out of frame.</span></div></div>
    </div>
  </div>
</section>

<section class="cta-band">
  <div class="wrap center">
    <h2>Start with your busiest site</h2>
    <p class="lede center-lede">Fourteen days, no card. Add the rest once you
      have seen a week of reports.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/pricing/">See pricing</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
