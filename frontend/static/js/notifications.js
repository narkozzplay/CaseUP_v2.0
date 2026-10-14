(function () {
    const POLL_INTERVAL_MS = 60000;

    function initBell(bell) {
        if (bell.dataset.notifBound === 'true') {
            return;
        }
        bell.dataset.notifBound = 'true';

        const toggle = bell.querySelector('[data-notif-toggle]');
        const panel = bell.querySelector('[data-notif-panel]');
        const badge = bell.querySelector('[data-notif-badge]');
        const countUrl = bell.dataset.notifCountUrl;

        function setOpen(open) {
            if (!toggle || !panel) {
                return;
            }
            panel.hidden = !open;
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            bell.classList.toggle('is-open', open);
        }

        function setCount(count) {
            if (!badge) {
                return;
            }
            const value = Number(count) || 0;
            badge.textContent = value > 99 ? '99+' : String(value);
            badge.hidden = value === 0;
        }

        if (toggle) {
            toggle.addEventListener('click', (event) => {
                event.stopPropagation();
                setOpen(panel.hidden);
            });
        }

        document.addEventListener('click', (event) => {
            if (panel && !panel.hidden && !bell.contains(event.target)) {
                setOpen(false);
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && panel && !panel.hidden) {
                setOpen(false);
                toggle.focus();
            }
        });

        // Clicking a link inside the panel navigates away: close it.
        if (panel) {
            panel.addEventListener('click', (event) => {
                if (event.target.closest('a')) {
                    setOpen(false);
                }
            });
        }

        // The topbar is rendered once, while the dashboard swaps page content
        // in place, so point "next" at wherever the user is right now.
        bell.addEventListener('submit', (event) => {
            const form = event.target.closest('[data-notif-action-form]');
            const next = form && form.querySelector('input[name="next"]');
            if (next) {
                next.value = window.location.pathname + window.location.search;
            }
        });

        document.addEventListener('dashboard:content-replaced', () => setOpen(false));

        async function refreshCount() {
            if (!countUrl || document.visibilityState !== 'visible') {
                return;
            }
            try {
                const response = await window.fetch(countUrl, {
                    credentials: 'same-origin',
                    headers: { Accept: 'application/json' },
                });
                if (!response.ok) {
                    return;
                }
                const payload = await response.json();
                setCount(payload.unread);
            } catch (error) {
                // Network hiccups are fine: the next tick or page load catches up.
            }
        }

        window.setInterval(refreshCount, POLL_INTERVAL_MS);
        document.addEventListener('visibilitychange', refreshCount);
    }

    document.querySelectorAll('[data-notif-bell]').forEach(initBell);
})();
