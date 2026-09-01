<?php
/**
 * WatchLog theme.
 *
 * Deliberately small. The marketing site is seven mostly-static pages;
 * a page builder would add weight, a plugin surface and an upgrade
 * treadmill for no benefit the customer can see.
 */

if (!defined('ABSPATH')) { exit; }

require_once get_template_directory() . '/icons.php';

function watchlog_setup() {
    add_theme_support('title-tag');
    add_theme_support('post-thumbnails');
    add_theme_support('html5', ['search-form', 'comment-list', 'gallery', 'caption', 'style', 'script']);
    register_nav_menus([
        'primary' => 'Primary',
        'footer'  => 'Footer',
    ]);
}
add_action('after_setup_theme', 'watchlog_setup');

function watchlog_assets() {
    // Geist is the geometric grotesque the identity calls for; Inter is
    // the fallback that is already on most machines.
    wp_enqueue_style('watchlog-fonts',
        'https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap',
        [], null);
    wp_enqueue_style('watchlog', get_stylesheet_uri(), ['watchlog-fonts'],
        wp_get_theme()->get('Version'));

    // Inner-page furniture: header band, prose, tables, TOC.
    if (!is_front_page()) {
        wp_enqueue_style('watchlog-pages',
            get_template_directory_uri() . '/pages.css', ['watchlog'],
            wp_get_theme()->get('Version'));
    }

    // Home page composition, loaded only where it is used.
    if (is_front_page()) {
        wp_enqueue_style('watchlog-home',
            get_template_directory_uri() . '/home.css', ['watchlog'],
            wp_get_theme()->get('Version'));
    }

    // Tiny progressive-enhancement script: sticky-header state, mobile
    // drawer, mega-menu a11y, reveal-on-scroll, count-up. No dependencies.
    wp_enqueue_script('watchlog',
        get_template_directory_uri() . '/theme.js', [],
        wp_get_theme()->get('Version'), true);
}
add_action('wp_enqueue_scripts', 'watchlog_assets');

/** Route helper: an absolute site URL with a trailing slash. */
function watchlog_url($path = '') {
    return esc_url(home_url('/' . ltrim($path, '/')));
}

/**
 * The monogram, inline.
 *
 * Inline rather than an <img> so it inherits colour from its context and
 * cannot flash in late on a cold cache. The path is generated from the
 * identity sheets by tools/build_brand_assets.py — do not hand-edit it.
 */
function watchlog_mark($height = 22) {
    $svg = get_template_directory() . '/mark.svg';
    if (!file_exists($svg)) { return ''; }
    return file_get_contents($svg);
}

/** Comments are off: this is a marketing site, not a blog. */
add_filter('comments_open', '__return_false', 20, 2);
add_filter('pings_open', '__return_false', 20, 2);

/** Trim WordPress's default head clutter we do not use. */
remove_action('wp_head', 'wp_generator');
remove_action('wp_head', 'wlwmanifest_link');
remove_action('wp_head', 'rsd_link');

/**
 * Where the portal lives, and the two doors into it.
 *
 * Every CTA on this site used to point at "#" because this option was
 * never set — the "no registration button" the buttons were there, they
 * just went nowhere. The base URL is an option so it can move without a
 * code change; the paths are split so "Sign in" and "Start a trial" go to
 * the right door instead of the same one.
 */
function watchlog_portal_base() {
    // Configurable: the WP option (set at provisioning from $WATCHLOG_PORTAL_URL),
    // else the env, else a neutral placeholder — never a hardcoded demo host.
    $default = getenv('WATCHLOG_PORTAL_URL') ?: 'https://watchlog.example';
    return rtrim(get_option('watchlog_portal_url', $default), '/');
}
function watchlog_signup_url() { return watchlog_portal_base() . '/signup/'; }
function watchlog_login_url()  { return watchlog_portal_base() . '/login/'; }

/**
 * Head: icons, and the meta a link needs to look like anything when it is
 * pasted into WhatsApp, LinkedIn or a search result. Without these the
 * site had no favicon, no description, and shared as a bare URL.
 */
function watchlog_head() {
    $t = get_template_directory_uri();
    $desc = 'The intelligence layer for the CCTV you already own. WatchLog turns '
          . 'your recorder\'s events into validated incidents, camera-health '
          . 'visibility and a daily report — without exposing your recorder to '
          . 'the internet. Works with Hikvision, Dahua and most ONVIF recorders.';
    $title = wp_get_document_title();
    $url = home_url(add_query_arg([], $GLOBALS['wp']->request ?? ''));
    $og  = "$t/img/og-card.png";
    ?>
    <link rel="icon" href="<?php echo esc_url("$t/img/favicon.ico"); ?>" sizes="16x16 32x32 48x48">
    <link rel="icon" href="<?php echo esc_url("$t/img/icon.svg"); ?>" type="image/svg+xml">
    <link rel="apple-touch-icon" href="<?php echo esc_url("$t/img/apple-touch-icon.png"); ?>">
    <meta name="description" content="<?php echo esc_attr($desc); ?>">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="WatchLog">
    <meta property="og:title" content="<?php echo esc_attr($title); ?>">
    <meta property="og:description" content="<?php echo esc_attr($desc); ?>">
    <meta property="og:image" content="<?php echo esc_url($og); ?>">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="<?php echo esc_attr($title); ?>">
    <meta name="twitter:description" content="<?php echo esc_attr($desc); ?>">
    <meta name="twitter:image" content="<?php echo esc_url($og); ?>">
    <link rel="canonical" href="<?php echo esc_url($url); ?>">
    <script type="application/ld+json"><?php echo wp_json_encode([
        '@context' => 'https://schema.org',
        '@type' => 'SoftwareApplication',
        'name' => 'WatchLog',
        'applicationCategory' => 'SecurityApplication',
        'operatingSystem' => 'Windows',
        'description' => $desc,
        'url' => home_url('/'),
        'offers' => [
            ['@type' => 'Offer', 'name' => 'Starter', 'price' => '6000', 'priceCurrency' => 'PKR',
             'description' => 'Per site, per month. 14-day free trial, no card.'],
            ['@type' => 'Offer', 'name' => 'Growth', 'price' => '12000', 'priceCurrency' => 'PKR',
             'description' => 'Per site, per month.'],
        ],
        'publisher' => ['@type' => 'Organization', 'name' => 'WatchLog'],
    ], JSON_UNESCAPED_SLASHES); ?></script>
    <script type="application/ld+json"><?php echo wp_json_encode([
        '@context' => 'https://schema.org',
        '@type' => 'Organization',
        'name' => 'WatchLog',
        'url' => home_url('/'),
        'logo' => "$t/icon-512.png",
        'description' => 'CCTV intelligence for businesses that already own cameras.',
    ], JSON_UNESCAPED_SLASHES); ?></script>
    <?php
}
add_action('wp_head', 'watchlog_head', 1);

/**
 * Redirect retired routes to their new homes (preserve any external links).
 */
function watchlog_redirects() {
    if (is_admin()) { return; }
    $map = [
        'features'    => '/platform/',
        'who-its-for' => '/solutions/',
        'about'       => '/security/',
    ];
    $req = trim(parse_url($_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH), '/');
    if (isset($map[$req])) {
        wp_safe_redirect(home_url($map[$req]), 301);
        exit;
    }
}
add_action('template_redirect', 'watchlog_redirects');

/**
 * A theme image, or nothing.
 *
 * Every photograph on this site is optional. The layouts were built to
 * stand up without them - segment tiles fall back to an icon, the hero
 * to a gradient - so a missing or not-yet-supplied file degrades the
 * page rather than breaking it. That also means the site can ship before
 * the photography exists, which is what happened.
 */
function watchlog_img_dir() { return get_template_directory() . '/img'; }
function watchlog_img_uri() { return get_template_directory_uri() . '/img'; }

/** The fallback file for a name: prefer .jpg, then .png, else ''. */
function watchlog_fallback($name) {
    foreach (['jpg', 'png'] as $ext) {
        if (file_exists(watchlog_img_dir() . "/$name.$ext")) { return $ext; }
    }
    return '';
}
function watchlog_has_img($name) { return watchlog_fallback($name) !== ''; }

/**
 * Responsive <picture>: WebP (full + `-sm` mobile source) with a JPG/PNG
 * fallback. Built by tools/build_site_images.py. `alt` is required; pass ''
 * only for genuinely decorative images. Missing files degrade to nothing so
 * a page can ship before every asset exists.
 */
function watchlog_pic($name, $alt, $w, $h, $class = '', $sizes = '100vw', $priority = false) {
    $ext = watchlog_fallback($name);
    if ($ext === '') { return ''; }
    $dir = watchlog_img_dir(); $uri = watchlog_img_uri();
    $srcset = [];
    if (file_exists("$dir/$name-sm.webp")) { $srcset[] = "$uri/$name-sm.webp 960w"; }
    if (file_exists("$dir/$name.webp"))    { $srcset[] = "$uri/$name.webp {$w}w"; }
    $load = $priority
        ? 'loading="eager" fetchpriority="high" decoding="async"'
        : 'loading="lazy" decoding="async"';
    $src = sprintf(
        '<img src="%s/%s.%s" alt="%s" width="%d" height="%d" class="%s" %s>',
        esc_url($uri), esc_attr($name), $ext, esc_attr($alt), $w, $h, esc_attr($class), $load);
    if ($srcset) {
        return sprintf(
            '<picture><source type="image/webp" srcset="%s" sizes="%s">%s</picture>',
            esc_attr(implode(', ', $srcset)), esc_attr($sizes), $src);
    }
    return $src;
}

/** Back-compat shim for older templates. */
function watchlog_img($name, $alt, $w, $h, $class = '') {
    return watchlog_pic($name, $alt, $w, $h, $class);
}

/**
 * A real product screenshot inside an app "frame". If the capture is not yet
 * present it renders a deliberate placeholder (mark + label) so the layout
 * holds and the page never looks broken before captures land.
 * $chrome=true adds the faux browser top bar.
 */
function watchlog_shot($name, $alt, $w, $h, $sizes = '100vw', $chrome = true, $priority = false) {
    $cls = $chrome ? 'frame' : 'frame-plain';
    $pic = watchlog_pic($name, $alt, $w, $h, 'shot-img', $sizes, $priority);
    if ($pic !== '') {
        return '<div class="' . $cls . '">' . $pic . '</div>';
    }
    // graceful placeholder
    return '<div class="' . $cls . ' shot-ph"><div class="shot-ph-in">'
        . watchlog_mark(30)
        . '<span>Live product view</span></div></div>';
}
