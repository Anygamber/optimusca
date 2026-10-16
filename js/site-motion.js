/**
 * Optimus CA — site motion (GSAP)
 * Skills: gsap-core, gsap-timeline, gsap-scrolltrigger, gsap-performance.
 * Vanilla HTML site — not React (gsap-react / gsap-frameworks unused here).
 */
(function (window, document) {
  'use strict';

  if (!window.gsap) {
    console.warn('[site-motion] GSAP not loaded');
    return;
  }

  var gsap = window.gsap;
  var ScrollTrigger = window.ScrollTrigger;

  if (ScrollTrigger) {
    gsap.registerPlugin(ScrollTrigger);
  }

  gsap.defaults({
    duration: 0.7,
    ease: 'power2.out',
    overwrite: 'auto'
  });

  /**
   * Subtle Glitch (variant 4) — premium RGB split + micro jitter.
   * Duration ~0.4s. Transform + opacity only (gsap-performance).
   */
  function playLogoGlitch() {
    var root = document.querySelector('[data-logo-glitch]');
    if (!root) return;

    var base = root.querySelector('.site-logo__img--base');
    var layerR = root.querySelector('.site-logo__img--r');
    var layerC = root.querySelector('.site-logo__img--c');
    if (!base || !layerR || !layerC) return;

    gsap.set([layerR, layerC], { autoAlpha: 0, x: 0, y: 0, force3D: true });
    gsap.set(base, { x: 0, y: 0, force3D: true });

    var tl = gsap.timeline({
      defaults: { ease: 'none', overwrite: true },
      onComplete: function () {
        gsap.set([layerR, layerC], { autoAlpha: 0, x: 0, y: 0, clearProps: 'will-change' });
        gsap.set(base, { x: 0, y: 0, clearProps: 'will-change' });
        root.classList.add('is-stable');
      }
    });

    /* Total ~0.42s — elegant, short chromatic aberration */
    tl.set(layerR, { autoAlpha: 0.5, x: -2 }, 0)
      .set(layerC, { autoAlpha: 0.5, x: 2 }, 0)
      .to(base, { x: 1.5, duration: 0.05 }, 0)
      .to(base, { x: -2, duration: 0.06 }, 0.05)
      .to(layerR, { x: -4, duration: 0.06 }, 0.05)
      .to(layerC, { x: 4, duration: 0.06 }, 0.05)
      .to(base, { x: 1, duration: 0.06 }, 0.11)
      .to(layerR, { x: -2, y: 0.5, duration: 0.07 }, 0.12)
      .to(layerC, { x: 2.5, y: -0.5, duration: 0.07 }, 0.12)
      .to(base, { x: 0, duration: 0.14, ease: 'power2.out' }, 0.22)
      .to(
        [layerR, layerC],
        { x: 0, y: 0, autoAlpha: 0, duration: 0.16, ease: 'power2.out' },
        0.24
      );
  }

  function boot() {
    var mm = gsap.matchMedia();

    mm.add('(prefers-reduced-motion: reduce)', function () {
      var logo = document.querySelector('[data-logo-glitch]');
      if (logo) logo.classList.add('is-stable');
      return function () {};
    });

    mm.add('(prefers-reduced-motion: no-preference)', function () {
      playLogoGlitch();

      var hero = document.getElementById('hero');

      /* 1) Hero timeline — from() keeps content visible if JS never runs */
      if (hero) {
        var tl = gsap.timeline({
          defaults: { duration: 0.75, ease: 'power3.out' },
          onComplete: function () {
            gsap.set(
              [
                '#hero .badge-row',
                '#hero-title',
                '#hero .hero-lead',
                '#hero .btn-equal',
                '#hero .hero-banner',
                '#hero .hero-form-card'
              ].join(','),
              { clearProps: 'will-change' }
            );
          }
        });

        tl.from('#hero .badge-row', { autoAlpha: 0, y: 24, duration: 0.55 })
          .from('#hero-title', { autoAlpha: 0, y: 28 }, '-=0.35')
          .from('#hero .hero-lead', { autoAlpha: 0, y: 24 }, '-=0.45')
          .from('#hero .btn-equal', { autoAlpha: 0, y: 20, stagger: 0.1 }, '-=0.4')
          .from(
            '#hero .hero-col-copy dl > div',
            { autoAlpha: 0, y: 16, stagger: 0.08, duration: 0.55 },
            '-=0.35'
          )
          .from('#hero .hero-banner', { autoAlpha: 0, y: 32, duration: 0.85 }, '-=0.55')
          .from('#hero .hero-form-card', { autoAlpha: 0, y: 28, duration: 0.8 }, '-=0.65');
      }

      /* 2) Scroll reveals for sections without card-batch (avoid double fade) */
      if (ScrollTrigger) {
        var skipIds = { products: 1, 'why-us': 1, contacts: 1, hero: 1 };
        var sections = gsap.utils.toArray('main.page-flow > section').filter(function (el) {
          return !skipIds[el.id];
        });

        sections.forEach(function (el) {
          gsap.from(el, {
            autoAlpha: 0,
            y: 40,
            duration: 0.8,
            ease: 'power2.out',
            scrollTrigger: {
              trigger: el,
              start: 'top 85%',
              toggleActions: 'play none none none',
              once: true
            }
          });
        });

        /* 3) Card grids — ScrollTrigger.batch + stagger */
        ScrollTrigger.batch(
          '#products .product-card, #why-us .card-glass, #contacts .card-glass',
          {
            start: 'top 90%',
            once: true,
            onEnter: function (elements) {
              gsap.from(elements, {
                autoAlpha: 0,
                y: 32,
                duration: 0.65,
                ease: 'power2.out',
                stagger: 0.1,
                overwrite: true
              });
            }
          }
        );
      }

      return function () {
        if (ScrollTrigger) {
          ScrollTrigger.getAll().forEach(function (st) {
            st.kill();
          });
        }
      };
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window, document);
