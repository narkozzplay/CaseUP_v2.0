(function () {
    'use strict';

    const SPIN_MS = 6200;
    const FAST_SPIN_MS = 1400;
    const LANE_STAGGER_MS = 260;
    const HISTORY_LIMIT = 8;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined && text !== null) {
            node.textContent = text;
        }
        return node;
    }

    function image(src, className) {
        const wrap = el('div', className);
        if (src) {
            const img = el('img');
            img.src = src;
            img.alt = '';
            img.addEventListener('error', () => img.remove());
            wrap.appendChild(img);
        }
        return wrap;
    }

    function money(root, amount) {
        const rate = Number(root.dataset.currencyRate || 1) || 1;
        const symbol = root.dataset.currencySymbol || '₽';
        return (Number(amount || 0) / rate).toFixed(2) + ' ' + symbol;
    }

    function csrf(root) {
        const input = root.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    async function post(root, url, data) {
        const body = new FormData();
        Object.entries(data || {}).forEach(([key, value]) => body.append(key, value));
        try {
            const response = await fetch(url, {
                method: 'POST',
                body,
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf(root), 'X-Requested-With': 'XMLHttpRequest' },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                return { ok: false, error: payload.error || 'Сервер не ответил. Попробуй ещё раз.' };
            }
            return payload;
        } catch (error) {
            return { ok: false, error: 'Нет связи с сервером. Попробуй ещё раз.' };
        }
    }

    function setBalance(root, value) {
        if (value === undefined || value === null) {
            return;
        }
        document.querySelectorAll('[data-sim-balance]').forEach((node) => {
            node.textContent = money(root, value);
        });
    }

    function wearText(result) {
        const parts = [];
        if (result.stattrak) {
            parts.push('StatTrak™');
        }
        if (result.exterior) {
            parts.push(result.exterior);
        }
        if (result.float) {
            parts.push(Number(result.float).toFixed(4));
        }
        return parts.join(' · ');
    }

    /* ------------------------------------------------------------------
       Case roulette
       ------------------------------------------------------------------ */

    function tile(data) {
        const node = el('div', 'sim-tile rarity--' + data.rarity);
        node.appendChild(image(data.image, 'sim-tile__media'));
        node.appendChild(el('span', 'sim-tile__weapon', data.weapon));
        node.appendChild(el('strong', 'sim-tile__name', data.skin));
        return node;
    }

    function prize(root, result, index) {
        const card = el('article', 'sim-prize rarity--' + result.rarity);
        card.style.animationDelay = (index * 110) + 'ms';
        if (result.stattrak) {
            card.appendChild(el('span', 'sim-prize__tag', 'StatTrak™'));
        }
        card.appendChild(image(result.image, 'sim-prize__media'));
        card.appendChild(el('span', 'sim-prize__weapon', result.weapon));
        card.appendChild(el('strong', 'sim-prize__name', result.skin));
        const wear = [result.exterior, result.float ? Number(result.float).toFixed(4) : ''].filter(Boolean).join(' · ');
        if (wear) {
            card.appendChild(el('span', 'sim-prize__wear', wear));
        }
        card.appendChild(el('span', 'sim-prize__value', money(root, result.value)));
        return card;
    }

    function historyTile(root, result, caseName) {
        const node = el('div', 'sim-drop rarity--' + result.rarity);
        node.title = result.display_name || result.name;
        node.appendChild(image(result.image, 'sim-drop__media'));
        const body = el('div', 'sim-drop__body');
        const weapon = el('span', 'sim-drop__weapon');
        if (result.stattrak) {
            weapon.appendChild(el('b', 'sim-st', 'ST'));
            weapon.appendChild(document.createTextNode(' '));
        }
        weapon.appendChild(document.createTextNode(result.weapon || ''));
        body.appendChild(weapon);
        body.appendChild(el('strong', 'sim-drop__name', result.skin));
        body.appendChild(el('span', 'sim-drop__meta', result.exterior || caseName || ''));
        node.appendChild(body);
        node.appendChild(el('span', 'sim-drop__value', money(root, result.value)));
        return node;
    }

    function selectedCount(root) {
        const checked = root.querySelector('input[name="sim-count"]:checked');
        return Math.max(1, Math.min(5, Number(checked ? checked.value : 1) || 1));
    }

    function setStatus(root, text, tone) {
        const status = root.querySelector('[data-sim-status]');
        if (status) {
            status.textContent = text || '';
            status.dataset.tone = tone || '';
        }
    }

    function buildLanes(roulette, strips) {
        const marker = roulette.querySelector('.sim-roulette__marker');
        const lanes = strips.map((strip) => {
            const lane = el('div', 'sim-lane');
            lane.dataset.simLane = '';
            const track = el('div', 'sim-lane__track');
            track.dataset.simTrack = '';
            strip.forEach((item) => track.appendChild(tile(item)));
            lane.appendChild(track);
            return lane;
        });
        roulette.dataset.lanes = String(strips.length);
        roulette.classList.remove('is-done');
        roulette.replaceChildren(marker, ...lanes);
        return lanes;
    }

    function spinLane(lane, winIndex, duration) {
        const track = lane.querySelector('[data-sim-track]');
        const first = track.children[0];
        if (!first) {
            return;
        }
        const tileWidth = first.getBoundingClientRect().width;
        const gap = parseFloat(getComputedStyle(track).columnGap || getComputedStyle(track).gap) || 0;
        const step = tileWidth + gap;
        const jitter = (Math.random() - 0.5) * tileWidth * 0.76;
        const offset = winIndex * step + tileWidth / 2 - lane.clientWidth / 2 + jitter;

        track.style.transition = 'none';
        track.style.transform = 'translate3d(0, 0, 0)';
        void track.offsetWidth;
        track.style.transition = 'transform ' + duration + 'ms cubic-bezier(0.08, 0.62, 0.1, 1)';
        track.style.transform = 'translate3d(' + (-offset) + 'px, 0, 0)';
    }

    function showReveal(root, payload) {
        const dialog = root.querySelector('[data-sim-reveal]');
        if (!dialog) {
            return;
        }
        const list = dialog.querySelector('[data-sim-reveal-list]');
        list.replaceChildren(...payload.results.map((result, index) => prize(root, result, index)));

        const title = dialog.querySelector('[data-sim-reveal-title]');
        title.textContent = payload.demo ? 'Демо-прокрут · баланс не списан' : (payload.results.length > 1 ? 'Твой дроп · ' + payload.results.length + ' предм.' : 'Твой дроп');

        const total = dialog.querySelector('[data-sim-reveal-total]');
        total.replaceChildren();
        if (payload.demo) {
            total.textContent = 'Оценка: ' + money(root, payload.total_value);
        } else {
            const spent = Number(payload.spent);
            const value = Number(payload.total_value);
            const bold = el('b', null, money(root, value));
            bold.dataset.tone = value >= spent ? 'up' : 'down';
            total.append('Потрачено ' + money(root, spent) + ' · дроп на ', bold);
        }

        const again = dialog.querySelector('[data-sim-again]');
        again.textContent = payload.demo ? 'Ещё демо' : 'Открыть ещё';

        const sell = dialog.querySelector('[data-sim-sell]');
        if (sell) {
            const ids = payload.results.map((result) => result.opening_id).filter(Boolean);
            sell.hidden = payload.demo || !ids.length || !root.dataset.sellUrl;
            sell.disabled = false;
            sell.classList.remove('is-sold');
            sell.dataset.ids = ids.join(',');
            sell.textContent = 'Продать за ' + money(root, payload.total_value);
        }
        if (typeof dialog.showModal === 'function') {
            dialog.showModal();
        } else {
            dialog.setAttribute('open', '');
        }
    }

    function pushHistory(root, results) {
        const box = root.querySelector('[data-sim-history]');
        if (!box) {
            return;
        }
        const empty = box.querySelector('[data-sim-history-empty]');
        if (empty) {
            empty.remove();
        }
        const caseName = root.querySelector('h1') ? root.querySelector('h1').textContent : '';
        results.slice().reverse().forEach((result) => box.prepend(historyTile(root, result, caseName)));
        while (box.children.length > HISTORY_LIMIT) {
            box.lastElementChild.remove();
        }
    }

    async function openCase(root, demo) {
        if (root.classList.contains('is-spinning')) {
            return;
        }
        const roulette = root.querySelector('[data-sim-roulette]');
        const count = selectedCount(root);
        root.dataset.lastDemo = demo ? '1' : '0';
        root.classList.add('is-spinning');
        setStatus(root, demo ? 'Крутим демо…' : 'Открываем…', '');

        const payload = await post(root, root.dataset.openUrl, { count: count, demo: demo ? '1' : '0' });
        if (!payload.ok) {
            root.classList.remove('is-spinning');
            setStatus(root, payload.error, 'error');
            return;
        }
        if (!demo) {
            setBalance(root, payload.balance);
        }

        const fast = root.querySelector('[data-sim-fast]');
        const duration = fast && fast.checked ? FAST_SPIN_MS : SPIN_MS;
        const lanes = buildLanes(roulette, payload.strips);
        window.requestAnimationFrame(() => {
            lanes.forEach((lane, index) => spinLane(lane, payload.win_index, duration + index * LANE_STAGGER_MS));
        });

        const total = duration + (lanes.length - 1) * LANE_STAGGER_MS;
        window.setTimeout(() => {
            lanes.forEach((lane) => {
                const winner = lane.querySelector('[data-sim-track]').children[payload.win_index];
                if (winner) {
                    winner.classList.add('is-winner');
                }
            });
            roulette.classList.add('is-done');
            setStatus(root, '', '');
            if (!demo) {
                pushHistory(root, payload.results);
            }
            window.setTimeout(() => {
                root.classList.remove('is-spinning');
                showReveal(root, payload);
            }, fast && fast.checked ? 250 : 650);
        }, total + 60);
    }

    document.addEventListener('click', (event) => {
        const root = event.target.closest('[data-case-sim]');
        if (!root) {
            return;
        }
        if (event.target.closest('[data-sim-open]')) {
            event.preventDefault();
            openCase(root, false);
            return;
        }
        if (event.target.closest('[data-sim-demo]')) {
            event.preventDefault();
            openCase(root, true);
            return;
        }
        const dialog = root.querySelector('[data-sim-reveal]');
        if (event.target.closest('[data-sim-close]') || event.target === dialog) {
            dialog.close();
            return;
        }
        if (event.target.closest('[data-sim-again]')) {
            dialog.close();
            openCase(root, root.dataset.lastDemo === '1');
            return;
        }
        const sell = event.target.closest('[data-sim-sell]');
        if (sell) {
            sellDrop(root, dialog, sell);
        }
    });

    async function sellDrop(root, dialog, button) {
        if (button.disabled || !button.dataset.ids) {
            return;
        }
        button.disabled = true;
        const body = new FormData();
        button.dataset.ids.split(',').forEach((id) => body.append('ids', id));
        let payload;
        try {
            const response = await fetch(root.dataset.sellUrl, {
                method: 'POST',
                body,
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-CSRFToken': csrf(root), 'X-Requested-With': 'XMLHttpRequest' },
            });
            payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                payload = { ok: false, error: payload.error || 'Не удалось продать. Попробуй ещё раз.' };
            }
        } catch (error) {
            payload = { ok: false, error: 'Нет связи с сервером. Попробуй ещё раз.' };
        }
        if (!payload.ok) {
            button.disabled = false;
            button.textContent = payload.error;
            return;
        }
        setBalance(root, payload.balance);
        button.classList.add('is-sold');
        button.textContent = 'Продано · +' + money(root, payload.total);
        dialog.querySelectorAll('.sim-prize').forEach((card) => card.classList.add('is-sold'));
    }

    document.addEventListener('change', (event) => {
        if (!event.target.matches('input[name="sim-count"]')) {
            return;
        }
        const root = event.target.closest('[data-case-sim]');
        const total = root && root.querySelector('[data-sim-total]');
        if (total) {
            total.textContent = money(root, Number(root.dataset.openPrice) * selectedCount(root));
        }
    });

    /* ------------------------------------------------------------------
       Terminal
       ------------------------------------------------------------------ */

    const typingQueues = new WeakMap();

    function termLog(root, text, tone, instant) {
        const log = root.querySelector('[data-term-log]');
        if (!log) {
            return Promise.resolve();
        }
        const previous = typingQueues.get(root) || Promise.resolve();
        const next = previous.then(() => new Promise((resolve) => {
            const line = el('p');
            if (tone) {
                line.dataset.tone = tone;
            }
            log.appendChild(line);
            while (log.children.length > 6) {
                log.firstElementChild.remove();
            }
            if (instant) {
                line.textContent = text;
                resolve();
                return;
            }
            line.classList.add('is-typing');
            let index = 0;
            const timer = window.setInterval(() => {
                index += 2;
                line.textContent = text.slice(0, index);
                if (index >= text.length) {
                    window.clearInterval(timer);
                    line.classList.remove('is-typing');
                    resolve();
                }
            }, 16);
        }));
        typingQueues.set(root, next);
        return next;
    }

    function termShow(root, view) {
        root.querySelectorAll('[data-term-view]').forEach((node) => {
            node.hidden = node.dataset.termView !== view;
        });
        const screen = root.querySelector('[data-term-root]');
        screen.dataset.state = view;
    }

    function termPips(root, state) {
        const box = root.querySelector('[data-term-pips]');
        if (!box) {
            return;
        }
        const total = state.offers_total || 5;
        const current = total - (state.offers_left || 0);
        box.replaceChildren();
        for (let i = 0; i < total; i += 1) {
            const pip = el('i', 'term-pip');
            if (state.status === 'burned' || i < current) {
                pip.classList.add('is-declined');
            }
            if (state.status === 'active' && i === current) {
                pip.classList.add('is-current');
            }
            if (state.status === 'accepted' && i === current) {
                pip.classList.remove('is-declined');
                pip.classList.add('is-accepted');
            }
            box.appendChild(pip);
        }
    }

    function fillOffer(root, offer) {
        const card = root.querySelector('[data-term-offer]');
        card.className = 'term-offer rarity--' + offer.rarity;
        void card.offsetWidth;
        card.classList.add('is-entering');

        const img = card.querySelector('[data-term-offer-img]');
        img.hidden = !offer.image;
        if (offer.image) {
            img.src = offer.image;
            img.onerror = () => { img.hidden = true; };
        }
        card.querySelector('[data-term-offer-step]').textContent = 'ПРЕДЛОЖЕНИЕ ' + offer.index + '/' + offer.total;
        card.querySelector('[data-term-offer-weapon]').textContent = offer.weapon;
        card.querySelector('[data-term-offer-name]').textContent = offer.skin;
        card.querySelector('[data-term-offer-wear]').textContent = wearText(offer);
        card.querySelector('[data-term-offer-value]').textContent = money(root, offer.value);
        card.querySelector('[data-term-offer-price]').textContent = money(root, offer.price);
        root.querySelector('[data-term-accept-price]').textContent = money(root, offer.price);
        root.querySelector('[data-term-warn]').hidden = offer.index < offer.total;
    }

    function fillResult(root, result) {
        const card = root.querySelector('[data-term-result]');
        card.className = 'term-offer term-offer--won rarity--' + result.rarity;
        const media = el('div', 'term-offer__media');
        if (result.image) {
            const img = el('img');
            img.src = result.image;
            img.alt = '';
            img.addEventListener('error', () => img.remove());
            media.appendChild(img);
        }
        const info = el('div', 'term-offer__info');
        info.appendChild(el('span', 'term-offer__step', 'ДОБАВЛЕНО В ИНВЕНТАРЬ'));
        info.appendChild(el('span', 'term-offer__weapon', result.weapon));
        info.appendChild(el('strong', 'term-offer__name', result.skin));
        info.appendChild(el('span', 'term-offer__wear', wearText(result)));
        const prices = el('dl', 'term-offer__prices');
        const value = el('div');
        value.append(el('dt', null, 'Оценка'), el('dd', null, money(root, result.value)));
        prices.appendChild(value);
        info.appendChild(prices);
        card.replaceChildren(media, info);
    }

    function termRender(root, state) {
        root.dataset.session = state.session || '';
        termPips(root, state);
        if (!state.session || !state.status) {
            termShow(root, 'idle');
            return;
        }
        if (state.status === 'active' && state.offer) {
            fillOffer(root, state.offer);
            termShow(root, 'offer');
            return;
        }
        if (state.status === 'accepted' && state.result) {
            fillResult(root, state.result);
            termShow(root, 'accepted');
            return;
        }
        termShow(root, 'burned');
    }

    function termError(root, text) {
        const box = root.querySelector('[data-term-error]');
        if (box) {
            box.hidden = !text;
            box.textContent = text || '';
        }
    }

    function sessionUrl(template, id) {
        return template.replace('/session/0/', '/session/' + id + '/');
    }

    async function termAction(root, action) {
        const screen = root.querySelector('[data-term-root]');
        if (screen.classList.contains('is-busy')) {
            return;
        }
        screen.classList.add('is-busy');
        termError(root, '');

        let url = root.dataset.startUrl;
        if (action === 'accept') {
            url = sessionUrl(root.dataset.acceptUrl, root.dataset.session);
        } else if (action === 'decline') {
            url = sessionUrl(root.dataset.declineUrl, root.dataset.session);
        }

        if (action === 'start') {
            termLog(root, 'Оплата доступа: ' + money(root, root.dataset.price));
            termLog(root, 'Устанавливаю соединение с торговцем…');
        } else if (action === 'decline') {
            termLog(root, 'Предложение отклонено.', 'bad');
        } else {
            termLog(root, 'Подтверждаю сделку…');
        }

        const payload = await post(root, url, {});
        if (!payload.ok) {
            await termLog(root, 'Ошибка: ' + payload.error, 'bad');
            termError(root, payload.error);
            screen.classList.remove('is-busy');
            return;
        }
        setBalance(root, payload.balance);

        if (payload.status === 'active' && payload.offer) {
            await termLog(root, 'Получаю предложение #' + payload.offer.index + '…');
        } else if (payload.status === 'accepted' && payload.result) {
            await termLog(root, 'Сделка закрыта: ' + payload.result.name + '.', 'good');
        } else if (payload.status === 'burned') {
            await termLog(root, 'Предложений больше нет. Соединение разорвано.', 'bad');
        }
        termRender(root, payload);
        screen.classList.remove('is-busy');
    }

    function initTerminal(root) {
        if (root.dataset.termReady) {
            return;
        }
        root.dataset.termReady = '1';
        const script = root.querySelector('#terminal-state');
        let state = {};
        try {
            state = script ? JSON.parse(script.textContent) : {};
        } catch (error) {
            state = {};
        }
        termRender(root, state);
        if (state.status === 'active') {
            termLog(root, 'Сессия восстановлена. Торговец ждёт ответа.', 'good', true);
        } else {
            termLog(root, 'Uplink готов. Ожидаю подключения.', null, true);
        }
    }

    function invSync(root) {
        const checks = Array.from(root.querySelectorAll('[data-sim-check]'));
        const picked = checks.filter((box) => box.checked);
        let total = 0;
        checks.forEach((box) => {
            const card = box.closest('[data-sim-inv]');
            if (card) {
                card.classList.toggle('is-selected', box.checked);
                if (box.checked) {
                    total += Number(card.dataset.value || 0);
                }
            }
        });
        const summary = root.querySelector('[data-sim-sell-summary]');
        if (summary) {
            if (picked.length) {
                summary.textContent = 'Выбрано: ' + picked.length + ' · ';
                summary.appendChild(el('b', null, money(root, total)));
            } else {
                summary.textContent = 'Отметь предметы, чтобы продать несколько сразу';
            }
        }
        const sellSelected = root.querySelector('[data-sim-sell-selected]');
        if (sellSelected) {
            sellSelected.disabled = picked.length === 0;
            sellSelected.textContent = picked.length ? 'Продать выбранные · ' + money(root, total) : 'Продать выбранные';
        }
        const all = root.querySelector('[data-sim-select-all]');
        if (all) {
            all.checked = checks.length > 0 && picked.length === checks.length;
            all.indeterminate = picked.length > 0 && picked.length < checks.length;
        }
    }

    function initInventory(root) {
        setBalance(root, root.dataset.balance);
        if (root.dataset.simInventoryBound === 'true') {
            return;
        }
        root.dataset.simInventoryBound = 'true';
        root.addEventListener('change', (event) => {
            if (event.target.matches('[data-sim-select-all]')) {
                root.querySelectorAll('[data-sim-check]').forEach((box) => {
                    box.checked = event.target.checked;
                });
            }
            if (event.target.matches('[data-sim-check], [data-sim-select-all]')) {
                invSync(root);
            }
        });
        root.addEventListener('click', (event) => {
            const confirmButton = event.target.closest('[data-sim-confirm]');
            if (confirmButton && !window.confirm(confirmButton.dataset.simConfirm)) {
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            const card = event.target.closest('[data-sim-inv]');
            if (!card || event.target.closest('button, a, input, label')) {
                return;
            }
            const box = card.querySelector('[data-sim-check]');
            if (box) {
                box.checked = !box.checked;
                invSync(root);
            }
        });
        invSync(root);
    }

    function initAll() {
        document.querySelectorAll('[data-terminal]').forEach(initTerminal);
        document.querySelectorAll('[data-sim-inventory]').forEach(initInventory);
    }

    document.addEventListener('click', (event) => {
        const root = event.target.closest('[data-terminal]');
        if (!root) {
            return;
        }
        if (event.target.closest('[data-term-start]')) {
            event.preventDefault();
            termAction(root, 'start');
        } else if (event.target.closest('[data-term-accept]')) {
            event.preventDefault();
            termAction(root, 'accept');
        } else if (event.target.closest('[data-term-decline]')) {
            event.preventDefault();
            termAction(root, 'decline');
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
    document.addEventListener('dashboard:content-replaced', initAll);
})();
