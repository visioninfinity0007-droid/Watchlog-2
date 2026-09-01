/* WatchLog theme — small, dependency-free progressive enhancement. */
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
})();
