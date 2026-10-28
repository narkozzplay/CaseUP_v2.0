(function () {
    'use strict';

    // Countdown to the Major start (or to the next stage lock).
    function pad(value) {
        return String(value).padStart(2, '0');
    }

    function tick(root) {
        const target = Date.parse(root.dataset.mjCountdown);
        if (Number.isNaN(target)) return;
        let left = Math.max(0, Math.floor((target - Date.now()) / 1000));
        const units = {
            days: Math.floor(left / 86400),
            hours: pad(Math.floor((left % 86400) / 3600)),
            minutes: pad(Math.floor((left % 3600) / 60)),
            seconds: pad(left % 60),
        };
        Object.entries(units).forEach(([unit, value]) => {
            const cell = root.querySelector(`[data-unit="${unit}"]`);
            if (cell) cell.textContent = value;
        });
        if (left === 0) root.classList.add('is-over');
    }

    let timer = null;
    function initCountdowns() {
        const roots = document.querySelectorAll('[data-mj-countdown]');
        if (timer) clearInterval(timer);
        if (!roots.length) return;
        roots.forEach(tick);
        timer = setInterval(() => roots.forEach(tick), 1000);
    }

    // Swiss stage: 2 × 3-0, 6 × advance, 2 × 0-3, shown in the slots on top.
    function swissState(form) {
        const counts = {};
        form.querySelectorAll('[data-slot]').forEach((group) => {
            counts[group.dataset.slot] = [];
        });
        form.querySelectorAll('.mj-pickrow').forEach((row) => {
            const checked = row.querySelector('input[type="radio"]:checked');
            if (checked && counts[checked.value]) counts[checked.value].push(row);
        });
        return counts;
    }

    function renderSwiss(form) {
        const state = swissState(form);
        form.querySelectorAll('[data-slot]').forEach((group) => {
            const rows = state[group.dataset.slot] || [];
            const limit = Number(group.dataset.limit || 0);
            group.querySelector('[data-count]').textContent = rows.length;
            group.classList.toggle('is-full', rows.length >= limit);
            const holder = group.querySelector('[data-teams]');
            holder.innerHTML = '';
            for (let index = 0; index < limit; index += 1) {
                const slot = document.createElement('span');
                slot.className = 'mj-slots__slot';
                const row = rows[index];
                if (row) {
                    slot.title = row.dataset.name;
                    if (row.dataset.logo) {
                        const img = document.createElement('img');
                        img.src = row.dataset.logo;
                        img.alt = row.dataset.name;
                        slot.appendChild(img);
                    } else {
                        slot.textContent = row.dataset.name.slice(0, 2);
                    }
                    slot.classList.add('is-filled');
                }
                holder.appendChild(slot);
            }
        });
        form.querySelectorAll('.mj-pickrow').forEach((row) => {
            row.classList.toggle('is-picked', Boolean(row.querySelector('input:checked')));
        });
    }

    function showError(form, text) {
        const box = form.querySelector('[data-mj-error]');
        if (!box) return;
        box.textContent = text;
        box.hidden = !text;
    }

    function initSwiss(form) {
        if (form.dataset.mjReady) return;
        form.dataset.mjReady = '1';
        let previous = null;
        let before = null;
        // A click on the checked option clears it (radios cannot be unchecked otherwise).
        form.addEventListener('pointerdown', (event) => {
            const label = event.target.closest('.mj-toggle__opt');
            const input = label && label.querySelector('input');
            previous = input && input.checked ? input : null;
            // The team's current pick, put back if the new one does not fit the limit.
            before = input ? form.querySelector(`input[name="${input.name}"]:checked`) : null;
        });
        form.addEventListener('click', (event) => {
            const label = event.target.closest('.mj-toggle__opt');
            if (!label) return;
            const input = label.querySelector('input');
            if (input && input === previous && !input.disabled) {
                event.preventDefault();
                input.checked = false;
                previous = null;
                showError(form, '');
                renderSwiss(form);
            }
        });
        form.addEventListener('change', (event) => {
            const input = event.target;
            if (input.type !== 'radio') return;
            const group = form.querySelector(`[data-slot="${input.value}"]`);
            const limit = Number(group ? group.dataset.limit : 0);
            const taken = swissState(form)[input.value] || [];
            if (limit && taken.length > limit) {
                input.checked = false;
                if (before && before !== input) before.checked = true;
                const label = group.querySelector('.mj-slots__label').textContent;
                showError(form, `«${label}»: уже выбрано ${limit}. Снимите одну из команд.`);
            } else {
                showError(form, '');
            }
            renderSwiss(form);
        });
        renderSwiss(form);
    }

    // Playoff bracket: later rounds offer the teams picked in the matches that feed them.
    function initBracket(form) {
        if (form.dataset.mjReady) return;
        form.dataset.mjReady = '1';
        let teams = {};
        try {
            teams = JSON.parse(form.querySelector('[data-mj-teams]').textContent || '{}');
        } catch (error) {
            teams = {};
        }
        const locked = !form.querySelector('button[type="submit"]');

        function picked(key) {
            const input = form.querySelector(`input[name="${key}"]:checked`);
            return input ? input.value : '';
        }

        function loserOf(key) {
            const winner = picked(key);
            if (!winner) return '';
            const other = Array.from(form.querySelectorAll(`input[name="${key}"]`)).find((input) => input.value && input.value !== winner);
            return other ? other.value : '';
        }

        function paint(label, teamId) {
            const input = label.querySelector('input');
            const view = label.querySelector('[data-team-view]');
            const team = teams[teamId];
            if (input.value !== teamId) input.checked = false;
            input.value = teamId || '';
            input.disabled = locked || !teamId;
            view.classList.toggle('mj-team--tbd', !team);
            view.innerHTML = '';
            if (team) {
                if (team.logo) {
                    const img = document.createElement('img');
                    img.className = 'mj-team__logo';
                    img.src = team.logo;
                    img.alt = '';
                    view.appendChild(img);
                }
                const name = document.createElement('span');
                name.className = 'mj-team__name';
                name.textContent = team.name;
                view.appendChild(name);
            } else {
                const name = document.createElement('span');
                name.className = 'mj-team__name';
                name.textContent = 'выберите выше';
                view.appendChild(name);
            }
        }

        function refresh() {
            // Semifinals before the final, the final before third place: each reads the round before it.
            ['r2s1', 'r2s2', 'r3s1', 'r4s1'].forEach((key) => {
                const box = form.querySelector(`[data-key="${key}"]`);
                if (!box) return;
                box.querySelectorAll('[data-winner-of]').forEach((label) => paint(label, picked(label.dataset.winnerOf)));
                box.querySelectorAll('[data-loser-of]').forEach((label) => paint(label, loserOf(label.dataset.loserOf)));
            });
            form.querySelectorAll('.mj-match--pick').forEach((box) => {
                box.classList.toggle('is-picked', Boolean(box.querySelector('input:checked')));
            });
        }

        form.addEventListener('change', (event) => {
            if (event.target.type === 'radio') refresh();
        });
        refresh();
    }

    // The banner loop starts from here, so it never plays for people who asked for less motion.
    function initBannerVideo() {
        const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (still) return;
        document.querySelectorAll('[data-mj-banner-video]').forEach((video) => {
            const played = video.play();
            if (played && played.catch) played.catch(() => {});
        });
    }

    function initAll() {
        initCountdowns();
        initBannerVideo();
        document.querySelectorAll('[data-mj-swiss]').forEach(initSwiss);
        document.querySelectorAll('[data-mj-bracket]').forEach(initBracket);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
    document.addEventListener('dashboard:content-replaced', initAll);
})();
