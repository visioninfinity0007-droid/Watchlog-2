<?php
if (!defined('ABSPATH')) { exit; }

/* Navigation model - single source for desktop mega-menu + mobile drawer. */
$WL_PRODUCT = [
    ['platform',    'layers',   'Platform',    'Every site in one view'],
    ['incidents',   'camera',   'Incidents',   'Validated, with a still'],
    ['reporting',   'report',   'Reporting',   'A daily summary that matters'],
    ['site-health', 'alert',    'Site Health', 'Know when a camera goes quiet'],
];
$WL_SOLUTIONS = [
    ['solutions',                        'sites',    'Overview',               'WatchLog across your sites'],
    ['solutions/warehouses-logistics',   'box',      'Warehouses & Logistics', 'After-hours and loading bays'],
    ['solutions/retail',                 'store',    'Retail',                 'Every branch in one view'],
    ['solutions/manufacturing',          'factory',  'Manufacturing',          'Shifts, gates, critical areas'],
    ['solutions/schools-campuses',       'school',   'Schools & Campuses',     'Boundaries, after hours'],
    ['solutions/offices',                'building', 'Offices & Commercial',   'Daily visibility, small sites'],
];
function wl_mega_item($it) {
    printf(
        '<a class="mega-item" href="%s"><span class="mi-ic">%s</span>'
        . '<span><span class="mi-t">%s</span><span class="mi-d">%s</span></span></a>',
        watchlog_url($it[0]), watchlog_icon($it[1], 20),
        esc_html($it[2]), esc_html($it[3]));
}
?>
<!doctype html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="<?php bloginfo('charset'); ?>">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#07111F">
<script>document.documentElement.className+=' js';</script>
<?php wp_head(); ?>
</head>
<body <?php body_class(); ?>>
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header" id="site-header">
  <div class="bar">
    <a class="brand" href="<?php echo esc_url(home_url('/')); ?>" aria-label="WatchLog home">
      <?php echo watchlog_mark(); ?><span>WatchLog</span>
    </a>

    <nav class="nav" aria-label="Primary">
      <div class="nav-item" data-menu>
        <button class="nav-link" aria-expanded="false" aria-haspopup="true">
          Product <?php echo watchlog_icon('chevron', 14, 'caret'); ?>
        </button>
        <div class="mega two" role="menu">
          <?php foreach ($WL_PRODUCT as $it) { wl_mega_item($it); } ?>
        </div>
      </div>

      <div class="nav-item" data-menu>
        <button class="nav-link" aria-expanded="false" aria-haspopup="true">
          Solutions <?php echo watchlog_icon('chevron', 14, 'caret'); ?>
        </button>
        <div class="mega two" role="menu">
          <?php foreach ($WL_SOLUTIONS as $it) { wl_mega_item($it); } ?>
        </div>
      </div>

      <a class="nav-link" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a>
      <a class="nav-link" href="<?php echo watchlog_url('compatibility'); ?>">Compatibility</a>
      <a class="nav-link" href="<?php echo watchlog_url('security'); ?>">Security</a>
      <a class="nav-link" href="<?php echo watchlog_url('pricing'); ?>">Pricing</a>
    </nav>

    <div class="nav-cta">
      <a class="nav-signin" href="<?php echo esc_url(watchlog_login_url()); ?>">Sign in</a>
      <a class="btn btn-primary" href="<?php echo esc_url(watchlog_signup_url()); ?>">Start free</a>
    </div>

    <button class="nav-toggle" id="nav-toggle" aria-label="Open menu" aria-expanded="false" aria-controls="m-drawer">
      <?php echo watchlog_icon('menu', 26); ?>
    </button>
  </div>
</header>

<!-- Mobile drawer -->
<div class="m-drawer" id="m-drawer" aria-hidden="true">
  <div class="m-top">
    <a class="brand" href="<?php echo esc_url(home_url('/')); ?>"><?php echo watchlog_mark(); ?><span>WatchLog</span></a>
    <button class="m-close" id="m-close" aria-label="Close menu"><?php echo watchlog_icon('close', 26); ?></button>
  </div>
  <div class="m-acc">
    <button aria-expanded="false">Product <?php echo watchlog_icon('chevron', 18, 'caret'); ?></button>
    <div class="m-sub">
      <?php foreach ($WL_PRODUCT as $it) { printf('<a href="%s">%s</a>', watchlog_url($it[0]), esc_html($it[2])); } ?>
    </div>
  </div>
  <div class="m-acc">
    <button aria-expanded="false">Solutions <?php echo watchlog_icon('chevron', 18, 'caret'); ?></button>
    <div class="m-sub">
      <?php foreach ($WL_SOLUTIONS as $it) { printf('<a href="%s">%s</a>', watchlog_url($it[0]), esc_html($it[2])); } ?>
    </div>
  </div>
  <a class="m-link" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a>
  <a class="m-link" href="<?php echo watchlog_url('compatibility'); ?>">Compatibility</a>
  <a class="m-link" href="<?php echo watchlog_url('security'); ?>">Security</a>
  <a class="m-link" href="<?php echo watchlog_url('pricing'); ?>">Pricing</a>
  <div class="m-cta">
    <a class="btn btn-primary btn-lg" href="<?php echo esc_url(watchlog_signup_url()); ?>">Start free</a>
    <a class="btn btn-ghost btn-lg" href="<?php echo esc_url(watchlog_login_url()); ?>">Sign in</a>
  </div>
</div>

<main id="main">
