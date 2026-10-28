(function () {
    'use strict';

    // Info banners: several banners in one place rotate every 7 s; dots switch by hand.
    const timers = [];

    function initRotator(root) {
        const slides = Array.from(root.querySelectorAll('[data-info-banner]'));
        const dots = Array.from(root.querySelectorAll('[data-info-dot]'));
        if (slides.length < 2) return;
        let index = 0;
        function show(next) {
            index = (next + slides.length) % slides.length;
            slides.forEach((slide, i) => slide.classList.toggle('is-active', i === index));
            dots.forEach((dot, i) => dot.classList.toggle('is-active', i === index));
        }
        dots.forEach((dot) => dot.addEventListener('click', () => show(Number(dot.dataset.infoDot))));
        let paused = false;
        root.addEventListener('mouseenter', () => { paused = true; });
        root.addEventListener('mouseleave', () => { paused = false; });
        timers.push(setInterval(() => { if (!paused) show(index + 1); }, 7000));
    }

    function initAll() {
        while (timers.length) clearInterval(timers.pop());
        const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        document.querySelectorAll('[data-info-banners]').forEach((root) => {
            if (still) {
                root.querySelectorAll('video').forEach((video) => video.pause());
            } else {
                initRotator(root);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
    document.addEventListener('dashboard:content-replaced', initAll);
})();
