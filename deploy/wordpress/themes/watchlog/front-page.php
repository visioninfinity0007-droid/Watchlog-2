<?php
/**
 * Homepage - WatchLog. Interactive physical-security SaaS experience.
 * Story: promise, proof, problem, product reveal (explorer), three outcomes
 * (sticky story), AI differentiation (demo), how it works, daily report,
 * multi-site, industry fit, trust, compatibility, pricing, conversion.
 * No em dashes in copy. Copy in docs/design/COPY_DECK.md.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());

/* --- data for the interactive product explorer (real demo captures) --- */
$WL_EXPLORER = [
  ['overview','Overview','product-platform-overview','Every site, one view.',
    'Incidents, after-hours activity, camera health and recent events across all your locations.',
    [['27','incidents, last 24h'],['92%','cameras online']]],
  ['incidents','Incidents','product-incidents','Only what is worth reviewing.',
    'Each kept event carries a still, filtered on site. Search by site, camera and type.',
    [['person','car, motorcycle'],['1 tap','to the moment']]],
  ['reports','Reports','product-reports','The morning brief, delivered.',
    'A daily summary to the right people on WhatsApp or email, with a full delivery history.',
    [['daily','per site'],['WhatsApp','and email']]],
  ['health','Site Health','product-site-health','Know when a camera goes quiet.',
    'Camera last-seen, recorder faults and whether each site is reporting at all.',
    [['last seen','per camera'],['faults','surfaced early']]],
];
/* --- three core outcomes (sticky story) --- */
$WL_STORY = [
  ['incidents','Incidents','camera','Review only the incidents worth seeing.',
    'A recorder logs hundreds of events a night. WatchLog keeps the ones with a person, car or motorcycle, each with a still, so what reaches you is worth opening.',
    'product-incidents','incidents'],
  ['reporting','Reporting','report','Start every morning already briefed.',
    'A short daily summary lands before your first coffee: counts by camera and type, after-hours activity, and anything that went quiet, in each site\'s local time.',
    'product-reports','reporting'],
  ['health','Site Health','alert','Know when a camera stops doing its job.',
    'The camera you rely on is usually the one that failed weeks ago. WatchLog surfaces silent cameras and reporting gaps far sooner.',
    'product-site-health','site-health'],
];
/* --- AI demo events (existing CCTV sample assets) --- */
$WL_AI = [
  ['person','cctv-person','Person','kept','A person on Main Gate after hours. Kept as an incident.'],
  ['vehicle','cctv-vehicle','Vehicle','kept','A vehicle at the loading bay. Kept as an incident.'],
  ['motorcycle','cctv-motorcycle','Motorcycle','kept','A motorcycle at the entrance. Kept as an incident.'],
  ['rain','cctv-empty-rain','Empty yard, rain','filtered','Rain on an empty yard tripped motion. Filtered at site.'],
  ['headlights','cctv-headlights','Headlights','filtered','Headlights sweeping a wall, no vehicle in view. Filtered at site.'],
];
/* --- multi-site selector --- */
$WL_SITES = [
  ['hq','Karachi Head Office','online','12','9','Person, Main Gate','22:16'],
  ['wh','Warehouse','online','8','14','Vehicle, Loading Bay','03:11'],
  ['ff','Factory Floor','fault','5','3','Motorcycle, Entrance','21:27'],
];
/* --- architecture stages (semantic icons; logo only for WatchLog) --- */
$WL_ARCH = [
  ['recorder','Recorder','Your existing NVR or DVR keeps recording as it always has.'],
  ['pc','Site Agent','A small Windows program on a PC at the site reads the recorder locally.'],
  ['check','Local filtering','Each still is checked on your own machine for a person, car or motorcycle.'],
  ['arrow-out','Outbound only','Only validated events and one still per incident are sent out. Nothing connects in.'],
  ['__mark__','WatchLog','Your private account receives the validated events and stills.'],
  ['report','Portal and reports','Your team sees incidents and site health, and gets a daily report.'],
];
?>

<!-- 01 · HERO -->
<section class="hero dark field glow-field">
  <?php echo watchlog_pic('hero-industry-atmosphere','',2000,1125,'hero-bg','100vw',true); ?>
  <div class="wrap-wide hero-grid">
    <div class="hero-copy">
      <span class="eyebrow">CCTV intelligence for businesses</span>
      <h1>Make your existing cameras useful every&nbsp;day.</h1>
      <p class="hero-sub">Know what happened overnight, what needs attention, and whether every camera is
        healthy. Filtered on your site, delivered every morning.</p>
      <div class="cta-row">
        <a class="btn btn-primary btn-xl" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-xl js-scroll" href="#explore">See WatchLog in action</a>
      </div>
      <ul class="chips">
        <li class="chip"><?php echo watchlog_icon('check',18); ?> Works with existing CCTV</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> On-site AI filtering</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> 14-day trial</li>
      </ul>
    </div>
    <div class="hero-visual">
      <div class="hv-main frame"><?php echo watchlog_pic('product-platform-overview','WatchLog Overview dashboard',1900,1200,'shot-img','(max-width:900px) 96vw, 640px',true); ?></div>
      <div class="hv-card hv-incident">
        <?php echo watchlog_pic('cctv-person','',1600,900,'hv-thumb','120px'); ?>
        <div><span class="hv-k">Incident kept</span><b>Person, Main Gate</b><span class="hv-t">22:16 · after hours</span></div>
      </div>
      <div class="hv-card hv-report">
        <span class="hv-ic"><?php echo watchlog_icon('check',18); ?></span>
        <div><b>Daily report delivered</b><span class="hv-t">WhatsApp and email</span></div>
      </div>
      <div class="hv-card hv-health">
        <span class="hv-dot"></span><div><b>92% cameras online</b><span class="hv-t">23 of 25</span></div>
      </div>
    </div>
  </div>
</section>

<!-- 02 · PROOF STRIP -->
<section class="dark navy proof s-xs">
  <div class="wrap-wide proof-grid">
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('camera',24); ?></span><div><b>Works with the CCTV you own</b><span>Hikvision, Dahua and most ONVIF recorders</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('check',24); ?></span><div><b>Filtered on site</b><span>False alarms dropped before anything is sent</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('lock',24); ?></span><div><b>No inbound access</b><span>Your recorder is never exposed to the internet</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('report',24); ?></span><div><b>A daily report</b><span>The useful part, on WhatsApp or email</span></div></div>
  </div>
</section>

<!-- 03 · PROBLEM -->
<section class="surface problem">
  <div class="wrap split split-5-7">
    <div class="reveal">
      <span class="eyebrow">The gap</span>
      <h2>Your CCTV records the evidence.<br>WatchLog turns it into visibility.</h2>
      <p class="lead">Most cameras are only ever opened after something has already gone wrong.</p>
      <p>WatchLog reads the events your recorder already logs and turns them into an operational record
        your team actually uses: a short daily read instead of hours of footage.</p>
      <div class="pb-contrast">
        <div class="pb-from"><span class="pb-tag">Today</span><b>Hours of footage</b><span>opened only after an incident</span></div>
        <span class="pb-arrow"><?php echo watchlog_icon('arrow-right',22); ?></span>
        <div class="pb-to"><span class="pb-tag">With WatchLog</span><b>A two-minute read</b><span>every morning, before the day starts</span></div>
      </div>
    </div>
    <div class="reveal problem-media">
      <?php echo watchlog_pic('environment-recorder','A CCTV recorder on a shelf at a site',1800,1200,'','(max-width:900px) 92vw, 52vw'); ?>
    </div>
  </div>
</section>

<!-- 04 · PRODUCT EXPLORER -->
<section class="dark field glow-field grid-bg explorer" id="explore">
  <div class="wrap-wide">
    <div class="sec-head center reveal">
      <span class="eyebrow">The platform</span>
      <h2>One place to understand every site.</h2>
      <p class="lead measure">Click through the surfaces your team lives in. Real screens from a WatchLog
        demo account.</p>
    </div>
    <div class="exp reveal" data-tabs>
      <div class="exp-tabs" role="tablist" aria-label="Platform surfaces">
        <?php foreach ($WL_EXPLORER as $k=>$e){ printf(
          '<button class="exp-tab%s" data-tab="%s" role="tab" aria-selected="%s">%s</button>',
          $k===0?' active':'', esc_attr($e[0]), $k===0?'true':'false', esc_html($e[1])); } ?>
      </div>
      <div class="exp-body">
        <div class="exp-stage">
          <?php foreach ($WL_EXPLORER as $k=>$e){ printf(
            '<figure class="exp-panel%s frame" data-panel="%s">%s</figure>',
            $k===0?' active':'', esc_attr($e[0]),
            watchlog_pic($e[2], $e[1].' screen in WatchLog', 1900,1200,'shot-img','(max-width:1100px) 92vw, 760px')); } ?>
        </div>
        <div class="exp-side">
          <?php foreach ($WL_EXPLORER as $k=>$e){
            printf('<div class="exp-caption%s" data-panel="%s"><h3>%s</h3><p>%s</p><div class="exp-metrics">',
              $k===0?' active':'', esc_attr($e[0]), esc_html($e[3]), esc_html($e[4]));
            foreach ($e[5] as $m){ printf('<div class="metric"><b>%s</b><span>%s</span></div>', esc_html($m[0]), esc_html($m[1])); }
            echo '</div></div>';
          } ?>
          <a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See the full platform <?php echo watchlog_icon('arrow-right',18); ?></a>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- 05 · THREE OUTCOMES (compact value trio; the explorer above is the product tour) -->
<section class="surface-cool outcomes">
  <div class="wrap">
    <div class="sec-head reveal"><span class="eyebrow">What you get</span>
      <h2>Three outcomes, not three features.</h2>
      <p class="lead measure">The product tour above shows the screens. Here is what they add up to.</p></div>
    <div class="grid g3 outcome-grid reveal">
      <?php foreach ($WL_STORY as $s){ printf(
        '<a class="outcome-card" href="%s"><span class="oc-ic">%s</span><h3>%s</h3><p>%s</p>'
        .'<span class="oc-more">See %s %s</span></a>',
        watchlog_url($s[6]), watchlog_icon($s[2],24), esc_html($s[3]), esc_html($s[4]),
        esc_html(strtolower($s[1])), watchlog_icon('arrow-right',16)); } ?>
    </div>
  </div>
</section>

<!-- 06 · AI DEMO -->
<section class="dark field glow-field grid-bg aidemo">
  <div class="wrap-wide">
    <div class="sec-head center reveal"><span class="eyebrow">On-site intelligence</span>
      <h2>Less noise. More useful incidents.</h2>
      <p class="lead measure">Rain, headlights and the IR lamp at dusk all trip motion. WatchLog checks
        each still on your own site PC and keeps only person, car and motorcycle. Try it.</p></div>
    <div class="ai reveal" data-aidemo>
      <div class="ai-flow"><span>Raw events</span><?php echo watchlog_icon('arrow-right',18); ?><span>WatchLog filter</span><?php echo watchlog_icon('arrow-right',18); ?><span>Useful incidents</span></div>
      <div class="ai-grid">
        <div class="ai-events" role="tablist" aria-label="Example events">
          <?php foreach ($WL_AI as $k=>$a){ printf(
            '<button class="ai-event%s" data-event="%s" data-verdict="%s" data-reason="%s" role="tab" aria-selected="%s">%s<span class="ai-lab">%s</span></button>',
            $k===0?' active':'', esc_attr($a[0]), esc_attr($a[3]), esc_attr($a[4]), $k===0?'true':'false',
            watchlog_pic($a[1], $a[2], 1600,900,'','120px'), esc_html($a[2])); } ?>
        </div>
        <div class="ai-stage">
          <div class="ai-shot frame"><?php echo watchlog_pic($WL_AI[0][1],'',1600,900,'shot-img js-ai-shot','(max-width:900px) 92vw, 520px'); ?></div>
          <div class="ai-verdict" data-verdict="kept">
            <span class="ai-badge js-ai-badge">Kept</span>
            <p class="js-ai-reason"><?php echo esc_html($WL_AI[0][4]); ?></p>
            <p class="ai-note">Detects person, car and motorcycle. Not facial recognition. If the detector cannot run, the event is kept rather than dropped.</p>
          </div>
        </div>
      </div>
      <p class="center" style="margin-top:34px"><a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">See how filtering becomes incidents <?php echo watchlog_icon('arrow-right',18); ?></a></p>
    </div>
  </div>
</section>

<!-- 07 · HOW IT WORKS (semantic architecture) -->
<section class="light arch-sec">
  <div class="wrap">
    <div class="sec-head reveal"><span class="eyebrow">How it works</span>
      <h2>Your recorder stays private.</h2>
      <p class="lead measure">Recorded video stays on the recorder. Recorder credentials stay on the site
        PC. WatchLog receives validated event data and one incident still when required.</p></div>
    <div class="arch reveal" data-arch>
      <?php foreach ($WL_ARCH as $k=>$n){
        $ic = $n[0]==='__mark__' ? '<span class="arch-mark">'.watchlog_mark(26).'</span>' : watchlog_icon($n[0],26);
        printf('<button class="arch-node%s%s" data-node="%s" data-text="%s" aria-label="%s"><span class="arch-ic">%s</span><span class="arch-name">%s</span></button>',
          $k===0?' active':'', $n[0]==='__mark__'?' is-wl':'', esc_attr($k), esc_attr($n[2]), esc_attr($n[1]), $ic, esc_html($n[1]));
        if ($k < count($WL_ARCH)-1) { echo '<span class="arch-arrow">'.watchlog_icon('arrow-right',20).'</span>'; }
      } ?>
    </div>
    <div class="arch-explain reveal"><p class="js-arch-text"><?php echo esc_html($WL_ARCH[0][2]); ?></p>
      <p class="arch-hint">Hover or tap a step to see what it does. The security promise behind this is on
        the <a href="<?php echo watchlog_url('security'); ?>">security page</a>.</p></div>
    <div class="arch-foot reveal center">
      <a class="btn btn-secondary" href="<?php echo watchlog_url('how-it-works'); ?>">How WatchLog works</a>
    </div>
  </div>
</section>

<!-- 08 · REPORTING -->
<section class="surface-cool report-sec">
  <div class="wrap split split-7-5">
    <div class="reveal report-media">
      <div class="report-frame frame"><?php echo watchlog_pic('product-reports','WatchLog daily report',1500,1050,'shot-img','(max-width:900px) 92vw, 55vw'); ?></div>
      <div class="report-channel" data-report>
        <span class="rc-label">Delivered on</span>
        <div class="rc-btns" role="tablist">
          <button class="rc-btn active" data-ch="whatsapp" role="tab" aria-selected="true"><?php echo watchlog_icon('whatsapp',18); ?> WhatsApp</button>
          <button class="rc-btn" data-ch="email" role="tab" aria-selected="false"><?php echo watchlog_icon('mail',18); ?> Email</button>
          <button class="rc-btn" data-ch="both" role="tab" aria-selected="false"><?php echo watchlog_icon('check',18); ?> Both</button>
        </div>
        <p class="rc-status js-rc-status">Sent to Operations Manager on WhatsApp at 07:00.</p>
      </div>
    </div>
    <div class="reveal">
      <span class="eyebrow">Every morning</span>
      <h2>The useful part, ready before your day starts.</h2>
      <p>A daily summary of what happened, delivered to the right people. Choose the channel per
        recipient, in each site's local time.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('report',20); ?> Daily summary and after-hours count</li>
        <li><?php echo watchlog_icon('alert',20); ?> Camera faults flagged</li>
        <li><?php echo watchlog_icon('people',20); ?> Per-recipient delivery and history</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<!-- 09 · MULTI-SITE -->
<section class="dark field glow-field multisite">
  <div class="wrap-wide">
    <div class="sec-head reveal"><span class="eyebrow">More than one site</span>
      <h2>See every location without chasing every site.</h2></div>
    <div class="ms reveal" data-sites>
      <div class="ms-chips" role="tablist" aria-label="Sites">
        <?php foreach ($WL_SITES as $k=>$s){ printf(
          '<button class="ms-chip%s" data-site="%s" role="tab" aria-selected="%s"><span class="ms-status %s"></span>%s</button>',
          $k===0?' active':'', esc_attr($s[0]), $k===0?'true':'false', esc_attr($s[2]), esc_html($s[1])); } ?>
      </div>
      <div class="ms-panel">
        <?php foreach ($WL_SITES as $k=>$s){
          $cams = (int) $s[3];
          $online = $s[2]==='online' ? $cams : max(0, $cams-1);
          $pct = $cams ? round($online / $cams * 100) : 0;
          $badge = $s[2]==='online'
            ? '<span class="ms-badge ok">'.watchlog_icon('check',15).' All online</span>'
            : '<span class="ms-badge bad">'.watchlog_icon('alert',15).' 1 camera fault</span>';
          printf('<div class="ms-detail%s" data-site="%s"><div class="ms-head"><h3>%s</h3>%s</div>'
            .'<div class="ms-stats"><div class="metric"><b>%s</b><span>cameras</span></div>'
            .'<div class="metric"><b>%s</b><span>incidents, 24h</span></div>'
            .'<div class="metric"><b>%s</b><span>last event</span></div></div>'
            .'<div class="ms-health"><div class="msh-row"><span>Camera health</span><span>%d / %d online</span></div>'
            .'<div class="msh-bar %s"><i style="width:%d%%"></i></div></div>'
            .'<p class="ms-last">%s Latest: %s</p></div>',
            $k===0?' active':'', esc_attr($s[0]), esc_html($s[1]), $badge,
            esc_html($s[3]), esc_html($s[4]), esc_html($s[6]),
            $online, $cams, $s[2]==='online'?'ok':'bad', $pct,
            watchlog_icon('clock',15), esc_html($s[5])); } ?>
      </div>
    </div>
    <p class="ms-foot reveal">Head office sees the pattern. Each site's report goes to the person who runs it, with roles for owner, admin and read-only.
      <a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See the platform <?php echo watchlog_icon('arrow-right',18); ?></a></p>
  </div>
</section>

<!-- 10 · SOLUTIONS -->
<section class="light solutions-sec">
  <div class="wrap-wide">
    <div class="sec-head reveal"><span class="eyebrow">Built for your sites</span>
      <h2>WatchLog for the sites you already operate.</h2></div>
    <div class="sol-editorial reveal">
      <?php
      $sols=[
        ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','See what happened after the shift ended.'],
        ['solutions/retail','solution-retail','Retail','Understand every branch without calling every branch.'],
        ['solutions/manufacturing','solution-manufacturing','Manufacturing','Visibility across shifts, gates and critical areas.'],
        ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Know what moved after hours.'],
        ['solutions/offices','solution-office-commercial','Offices & Commercial','Daily visibility without watching screens.'],
      ];
      foreach ($sols as $i=>$s){ printf(
        '<a class="sol-card%s" href="%s">%s<div class="sol-body"><span class="sol-ind">%s</span><p>%s</p>'
        .'<span class="sol-more">Explore %s %s</span></div></a>',
        $i===0?' sol-lead':'', watchlog_url($s[0]),
        watchlog_pic($s[1],$s[2],1800,1200,'sol-img', $i===0?'(max-width:900px) 92vw, 50vw':'(max-width:900px) 92vw, 25vw'),
        esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])), watchlog_icon('arrow-right',16)); }
      ?>
    </div>
  </div>
</section>

<!-- 11 · SECURITY (one trust chapter) -->
<section class="dark navy glow-field trust">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Security</span>
      <h2>Security by architecture, not by promise.</h2>
      <p class="lead measure">The sensitive parts never have to move. WatchLog only ever holds validated
        event data and one still per incident.</p></div>
    <div class="trust-grid reveal">
      <div class="trust-item"><?php echo watchlog_icon('shield',24); ?><b>No public exposure</b><span>Your recorder is never reachable from the internet.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('lock',24); ?><b>Credentials stay on site</b><span>The recorder login lives on the site PC, never sent to us.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('camera',24); ?><b>Footage stays local</b><span>Recorded video stays on your recorder. We cannot browse it.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('check',24); ?><b>Isolated and tested</b><span>Per-customer isolation, verified by an automated test before every release.</span></div>
    </div>
    <div class="center reveal" style="margin-top:36px"><a class="btn btn-ghost" href="<?php echo watchlog_url('security'); ?>">Explore security</a></div>
  </div>
</section>

<!-- 12 · COMPATIBILITY -->
<section class="surface compat-sec">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Compatibility</span>
      <h2>Keep the cameras. Keep the recorder. Add WatchLog.</h2></div>
    <div class="compat-switch reveal" data-compat>
      <div class="cs-toggle" role="tablist">
        <button class="cs-btn active" data-c="know" role="tab" aria-selected="true">I know my recorder</button>
        <button class="cs-btn" data-c="unsure" role="tab" aria-selected="false">I am not sure</button>
      </div>
      <div class="cs-panel active" data-c="know">
        <div class="compat-brands">
          <span class="cb v"><?php echo watchlog_icon('check',15); ?> Hikvision</span>
          <span class="cb v"><?php echo watchlog_icon('check',15); ?> Dahua</span>
          <span class="cb p">HiLook</span>
          <span class="cb p">Imou</span>
          <span class="cb p">CP&nbsp;Plus</span>
          <span class="cb p">Uniview</span>
          <span class="cb p">Tiandy</span>
          <span class="cb p">ONVIF</span>
        </div>
        <p class="cb-legend"><span class="cb-key v"></span> Validated in the field
          <span class="cb-key p"></span> Protocol-compatible, confirmed at install</p>
        <p class="cs-note">The install is the real test and takes about ten minutes.</p>
        <div class="cta-row"><a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
          <a class="btn btn-secondary" href="<?php echo watchlog_url('compatibility'); ?>">See the full list</a></div>
      </div>
      <div class="cs-panel" data-c="unsure">
        <p>No problem. Send us a photo of the label on the front or back of your recorder and we will
          confirm compatibility, usually the same day, before you spend anything.</p>
        <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
      </div>
    </div>
  </div>
</section>

<!-- 13 · PRICING -->
<section class="surface-cool pricing-sec">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Pricing</span>
      <h2>Start with one site. Prove the value in 14 days.</h2>
      <p class="lead measure">Priced per site, in rupees. No card for the trial. When a trial ends,
        reporting pauses and your events and history are kept.</p></div>
    <div class="price-helper reveal" data-helper>
      <span class="ph-label">How many cameras at your site?</span>
      <div class="ph-btns" role="tablist">
        <button class="ph-btn" data-plan="starter" role="tab">8 or fewer</button>
        <button class="ph-btn" data-plan="growth" role="tab">9 to 24</button>
        <button class="ph-btn" data-plan="enterprise" role="tab">More than 24</button>
      </div>
    </div>
    <div class="grid g3 price-grid reveal">
      <div class="price-card" data-plan="starter">
        <h3>Starter</h3><p class="price"><span>PKR</span> 6,000<small>per site / month</small></p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('camera',18); ?> Up to 8 cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 7 days of stills</li>
          <li><?php echo watchlog_icon('report',18); ?> WhatsApp and email reports</li></ul>
        <a class="btn btn-secondary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card featured" data-plan="growth">
        <span class="price-tag">Most sites</span>
        <h3>Growth</h3><p class="price"><span>PKR</span> 12,000<small>per site / month</small></p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('camera',18); ?> Up to 24 cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 30 days of stills</li>
          <li><?php echo watchlog_icon('chart',18); ?> Analytics and site health</li></ul>
        <a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card" data-plan="enterprise">
        <h3>Enterprise</h3><p class="price price-talk">Talk to us</p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('camera',18); ?> Unlimited cameras</li>
          <li><?php echo watchlog_icon('clock',18); ?> 90 days of stills</li>
          <li><?php echo watchlog_icon('sites',18); ?> Central multi-site view</li></ul>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
      </div>
    </div>
    <p class="center" style="margin-top:22px"><a class="arrow-link" href="<?php echo watchlog_url('pricing'); ?>">View full pricing <?php echo watchlog_icon('arrow-right',18); ?></a></p>
  </div>
</section>

<!-- 14 · FINAL CTA -->
<section class="dark field glow-field final-cta">
  <?php echo watchlog_pic('hero-industry-atmosphere','',2000,1125,'cta-bg','100vw'); ?>
  <div class="wrap center">
    <h2>See what your cameras have been telling you.</h2>
    <p class="lead measure">Connect one site, let WatchLog do the filtering, and start each day with a
      clearer security picture.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-xl" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-xl" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
