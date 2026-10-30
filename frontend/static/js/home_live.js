// «Сейчас на CaseUP» on the home page: refresh the cards every few seconds, new ones slide in.
(function () {
    'use strict';

    const POLL_MS = 8000;

    function keys(grid) {
        return new Set(Array.from(grid.querySelectorAll('[data-key]'), (card) => card.dataset.key));
    }

    async function refresh(grid) {
        if (document.visibilityState !== 'visible') {
            return;
        }
        try {
            const response = await fetch(grid.dataset.url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } });
            const payload = response.ok ? await response.json() : null;
            if (!payload || !payload.ok || !payload.html.trim()) {
                return;
            }
            const seen = keys(grid);
            grid.innerHTML = payload.html;
            grid.querySelectorAll('[data-key]').forEach((card) => {
                if (!seen.has(card.dataset.key)) {
                    card.classList.add('is-new');
                }
            });
        } catch (error) {
            // Offline for a moment: the next tick tries again.
        }
    }

    function init() {
        const grid = document.querySelector('[data-home-live]');
        if (grid) {
            window.setInterval(() => refresh(grid), POLL_MS);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
