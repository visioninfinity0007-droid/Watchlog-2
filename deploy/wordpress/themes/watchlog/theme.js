/* WatchLog theme - small, dependency-free progressive enhancement. */
(function () {
  'use strict';
  var doc = document;

  /* 1. Sticky header state */
  var header = doc.getElementById('site-header');
  if (header) {
    var onScroll = function () {
      header.classList.toggle('scrolled', window.scrollY > 8);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  /* 2. Mobile drawer */
  var drawer = doc.getElementById('m-drawer');
  var toggle = doc.getElementById('nav-toggle');
  var close = doc.getElementById('m-close');
  function setDrawer(open) {
    if (!drawer) return;
    drawer.classList.toggle('open', open);
    drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    doc.body.style.overflow = open ? 'hidden' : '';
  }
  if (toggle) toggle.addEventListener('click', function () { setDrawer(true); });
  if (close) close.addEventListener('click', function () { setDrawer(false); });

  /* 3. Mobile accordions */
  Array.prototype.forEach.call(doc.querySelectorAll('.m-acc > button'), function (btn) {
    btn.addEventListener('click', function () {
      var acc = btn.parentNode;
      var open = acc.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });

  /* 4. Desktop mega-menu: click/keyboard support + aria sync */
  var openItem = null;
  function closeMenus() {
    if (openItem) {
      openItem.classList.remove('open');
      var b = openItem.querySelector('.nav-link');
      if (b) b.setAttribute('aria-expanded', 'false');
      openItem = null;
    }
  }
  Array.prototype.forEach.call(doc.querySelectorAll('.nav-item[data-menu]'), function (item) {
    var btn = item.querySelector('.nav-link');
    if (!btn) return;
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var isOpen = item.classList.contains('open');
      closeMenus();
      if (!isOpen) {
        item.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
        openItem = item;
      }
    });
  });
  doc.addEventListener('click', function (e) {
    if (openItem && !openItem.contains(e.target)) closeMenus();
  });
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeMenus(); setDrawer(false); }
  });

  /* 5. Reveal on scroll */
  var reveals = doc.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && reveals.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.12 });
    Array.prototype.forEach.call(reveals, function (el) { io.observe(el); });
  } else {
    Array.prototype.forEach.call(reveals, function (el) { el.classList.add('in'); });
  }

  /* 6. Count-up once */
  var counts = doc.querySelectorAll('[data-count]');
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (counts.length && 'IntersectionObserver' in window) {
    var co = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target, target = parseFloat(el.getAttribute('data-count')) || 0;
        var suffix = el.getAttribute('data-suffix') || '';
        co.unobserve(el);
        if (reduce) { el.textContent = target + suffix; return; }
        var start = null, dur = 1100;
        function step(ts) {
          if (!start) start = ts;
          var p = Math.min((ts - start) / dur, 1);
          var val = Math.round(target * (0.5 - Math.cos(p * Math.PI) / 2));
          el.textContent = val + suffix;
          if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      });
    }, { threshold: 0.6 });
    Array.prototype.forEach.call(counts, function (el) { co.observe(el); });
  }

  /* 7. Tabs: [data-tabs] with [data-tab] buttons + [data-panel] panels */
  Array.prototype.forEach.call(doc.querySelectorAll('[data-tabs]'), function (group) {
    var tabs = group.querySelectorAll('[data-tab]');
    var panels = group.querySelectorAll('[data-panel]');
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener('click', function () {
        var key = tab.getAttribute('data-tab');
        Array.prototype.forEach.call(tabs, function (t) {
          var on = t === tab;
          t.classList.toggle('active', on);
          t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        Array.prototype.forEach.call(panels, function (p) {
          p.classList.toggle('active', p.getAttribute('data-panel') === key);
        });
      });
    });
  });

  var $ = function (s, r) { return (r || doc).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || doc).querySelectorAll(s)); };
  function setActive(list, el, attr) {
    list.forEach(function (n) {
      var on = n === el; n.classList.toggle('active', on);
      if (attr) n.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  /* 8. Smooth in-page scroll (respects fixed header) */
  $$('.js-scroll').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var id = a.getAttribute('href'); if (!id || id[0] !== '#') return;
      var t = doc.getElementById(id.slice(1)); if (!t) return;
      e.preventDefault();
      var y = t.getBoundingClientRect().top + window.scrollY - 76;
      window.scrollTo({ top: y, behavior: reduce ? 'auto' : 'smooth' });
    });
  });

  /* 9. AI filter demo */
  var aidemo = $('[data-aidemo]');
  if (aidemo) {
    var aEvents = $$('.ai-event', aidemo);
    var aVerdict = $('.ai-verdict', aidemo), aBadge = $('.js-ai-badge', aidemo),
        aReason = $('.js-ai-reason', aidemo);
    var aImg = $('.js-ai-shot', aidemo), aSrc = aImg && aImg.parentNode.querySelector('source');
    aEvents.forEach(function (btn) {
      btn.addEventListener('click', function () {
        setActive(aEvents, btn, true);
        var v = btn.getAttribute('data-verdict');
        aVerdict.setAttribute('data-verdict', v);
        aBadge.textContent = v === 'kept' ? 'Kept' : 'Filtered at site';
        aReason.textContent = btn.getAttribute('data-reason') || '';
        var es = btn.querySelector('source'), ei = btn.querySelector('img');
        if (aSrc && es) aSrc.srcset = es.srcset;
        if (aImg && ei) aImg.src = ei.src;
      });
    });
  }

  /* 10. Architecture nodes */
  var arch = $('[data-arch]');
  if (arch) {
    var aNodes = $$('.arch-node', arch), aText = $('.js-arch-text');
    var show = function (btn) { setActive(aNodes, btn); if (aText) aText.textContent = btn.getAttribute('data-text') || ''; };
    aNodes.forEach(function (btn) {
      btn.addEventListener('mouseenter', function () { show(btn); });
      btn.addEventListener('focus', function () { show(btn); });
      btn.addEventListener('click', function () { show(btn); });
    });
  }

  /* 11. Reporting channel */
  var rep = $('[data-report]');
  if (rep) {
    var rBtns = $$('.rc-btn', rep), rStatus = $('.js-rc-status', rep);
    var msg = {
      whatsapp: 'Sent to Operations Manager on WhatsApp at 07:00.',
      email: 'Sent to Head Office by email at 07:00.',
      both: 'Sent to Operations Manager on WhatsApp and Head Office by email at 07:00.'
    };
    rBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        setActive(rBtns, btn, true);
        if (rStatus) rStatus.textContent = msg[btn.getAttribute('data-ch')] || '';
      });
    });
  }

  /* 12. Multi-site selector */
  var sites = $('[data-sites]');
  if (sites) {
    var sChips = $$('.ms-chip', sites), sDetails = $$('.ms-detail', sites);
    sChips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        setActive(sChips, chip, true);
        var k = chip.getAttribute('data-site');
        sDetails.forEach(function (d) { d.classList.toggle('active', d.getAttribute('data-site') === k); });
      });
    });
  }

  /* 13. Compatibility toggle */
  var compat = $('[data-compat]');
  if (compat) {
    var cBtns = $$('.cs-btn', compat), cPanels = $$('.cs-panel', compat);
    cBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        setActive(cBtns, btn, true);
        var k = btn.getAttribute('data-c');
        cPanels.forEach(function (p) { p.classList.toggle('active', p.getAttribute('data-c') === k); });
      });
    });
  }

  /* 14. Pricing helper */
  var helper = $('[data-helper]');
  if (helper) {
    var hBtns = $$('.ph-btn', helper), cards = $$('.price-card');
    hBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        setActive(hBtns, btn, true);
        var k = btn.getAttribute('data-plan');
        cards.forEach(function (c) { c.classList.toggle('is-match', c.getAttribute('data-plan') === k); });
      });
    });
  }

  /* 15. Sticky story: sync visual + step to scroll position */
  var story = $('[data-story]');
  if (story && 'IntersectionObserver' in window) {
    var steps = $$('.story-step', story), shots = $$('.story-shot', story);
    var byKey = function (list, k) { return list.filter(function (n) { return n.getAttribute('data-step') === k || n.getAttribute('data-shot') === k; })[0]; };
    var so = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var k = en.target.getAttribute('data-step');
        steps.forEach(function (s) { s.classList.toggle('active', s === en.target); });
        shots.forEach(function (sh) { sh.classList.toggle('active', sh.getAttribute('data-shot') === k); });
      });
    }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
    steps.forEach(function (s) { so.observe(s); });
  }
})();
