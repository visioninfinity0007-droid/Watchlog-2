<?php
/**
 * Default page template.
 *
 * Used by the prose pages — about, privacy, terms — and as the fallback
 * for anything without a bespoke template. The marketing pages have
 * their own (page-pricing.php and friends).
 *
 * Two things make a long text page readable rather than a wall:
 * a header band that tells you where you are, and a table of contents
 * built from the page's own <h2>s so it can be scanned without being
 * read. The TOC is generated from the content, not hand-maintained, so
 * it cannot drift out of date.
 */
if (!defined('ABSPATH')) { exit; }
get_header();

while (have_posts()) : the_post();

  $content = apply_filters('the_content', get_the_content());

  // Pull the h2s out for the contents list, and give each one an id to
  // link to. Done here rather than in JS so it works with JS disabled
  // and is present for search engines.
  $headings = [];
  $content = preg_replace_callback(
      '/<h2([^>]*)>(.*?)<\/h2>/is',
      function ($m) use (&$headings) {
          $text = trim(wp_strip_all_tags($m[2]));
          $slug = sanitize_title($text);
          if ($slug === '') { return $m[0]; }
          $headings[] = ['slug' => $slug, 'text' => $text];
          return '<h2 id="' . esc_attr($slug) . '"' . $m[1] . '>' . $m[2] . '</h2>';
      },
      $content);

  // The first paragraph is written as the lede and belongs in the header
  // band, not repeated in the body.
  $lede = '';
  if (preg_match('/<p class="lede">(.*?)<\/p>/is', $content, $m)) {
      $lede = $m[1];
      $content = str_replace($m[0], '', $content);
  }
  ?>

  <section class="page-hero">
    <div class="wrap">
      <nav class="crumbs" aria-label="Breadcrumb">
        <a href="<?php echo esc_url(home_url('/')); ?>">Home</a>
        <span>/</span><?php the_title(); ?>
      </nav>
      <h1><?php the_title(); ?></h1>
      <?php if ($lede) : ?><p class="lede"><?php echo wp_kses_post($lede); ?></p><?php endif; ?>
    </div>
  </section>

  <article class="prose">
    <div class="wrap">
      <div class="prose-grid">
        <?php if (count($headings) > 2) : ?>
          <nav class="toc" aria-label="On this page">
            <div class="toc-title">On this page</div>
            <ul>
              <?php foreach ($headings as $h) : ?>
                <li><a href="#<?php echo esc_attr($h['slug']); ?>"><?php echo esc_html($h['text']); ?></a></li>
              <?php endforeach; ?>
            </ul>
          </nav>
        <?php else : ?>
          <div aria-hidden="true"></div>
        <?php endif; ?>

        <div class="prose-body"><?php echo wp_kses_post($content); ?></div>
      </div>
    </div>
  </article>

<?php endwhile; ?>

<section class="cta-band">
  <div class="wrap center">
    <h2>Fourteen days, no card</h2>
    <p class="lede center-lede">If WatchLog does not work with your recorder
      you will know within ten minutes.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url(get_option('watchlog_portal_url', '#')); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/contact/">Talk to us</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
