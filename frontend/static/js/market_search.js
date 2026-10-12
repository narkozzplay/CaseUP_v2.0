(function () {
    'use strict';

    const DEBOUNCE_MS = 350;
    const MIN_QUERY = 2;
    const timers = new WeakMap();
    const requests = new WeakMap();

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

    function setStatus(root, text, tone) {
        const status = root.querySelector('[data-market-search-status]');
        if (!status) {
            return;
        }
        status.textContent = text;
        status.dataset.tone = tone || '';
    }

    function buildAddForm(root, result) {
        const form = el('form', 'market-result__form');
        form.method = 'post';
        form.action = root.dataset.addUrl;

        const csrf = root.querySelector('input[name="csrfmiddlewaretoken"]');
        if (csrf) {
            const token = el('input');
            token.type = 'hidden';
            token.name = 'csrfmiddlewaretoken';
            token.value = csrf.value;
            form.appendChild(token);
        }

        const value = el('input');
        value.type = 'hidden';
        value.name = 'value';
        value.value = result.hash_name;
        form.appendChild(value);

        const stock = el('input');
        stock.type = 'hidden';
        stock.name = 'stock';
        stock.value = '0';
        form.appendChild(stock);

        const button = el('button', 'btn btn-primary btn-sm', 'Добавить');
        button.type = 'submit';
        form.appendChild(button);

        form.addEventListener('submit', () => {
            button.disabled = true;
            button.textContent = 'Добавляю…';
        });
        return form;
    }

    function renderResult(root, result) {
        const card = el('article', 'market-result' + (result.rarity_key ? ' rarity--' + result.rarity_key : ''));

        const media = el('div', 'market-result__media');
        if (result.icon) {
            const img = el('img');
            img.src = result.icon;
            img.alt = result.name;
            img.loading = 'lazy';
            media.appendChild(img);
        }
        card.appendChild(media);

        const body = el('div', 'market-result__body');
        body.appendChild(el('strong', 'market-result__name', result.name));
        body.appendChild(el('span', 'market-result__type', result.rarity_label || result.type_label || result.item_type));
        const meta = el('span', 'market-result__meta');
        if (result.price_text) {
            meta.appendChild(el('span', 'market-result__price', 'от ' + result.price_text));
        }
        if (result.listings) {
            meta.appendChild(el('span', null, result.listings + ' лотов'));
        }
        body.appendChild(meta);
        card.appendChild(body);

        const action = el('div', 'market-result__action');
        if (result.catalog_url) {
            const link = el('a', 'btn btn-ghost btn-sm', 'В каталоге →');
            link.href = result.catalog_url;
            action.appendChild(link);
        } else {
            action.appendChild(buildAddForm(root, result));
        }
        card.appendChild(action);
        return card;
    }

    async function runSearch(root, query) {
        const list = root.querySelector('[data-market-search-results]');
        if (!list) {
            return;
        }

        if (query.length < MIN_QUERY) {
            list.replaceChildren();
            setStatus(root, 'Введи хотя бы 2 символа.', '');
            return;
        }

        const previous = requests.get(root);
        if (previous) {
            previous.abort();
        }
        const controller = new AbortController();
        requests.set(root, controller);

        root.classList.add('is-loading');
        setStatus(root, 'Ищу в Steam Market…', '');

        try {
            const url = root.dataset.endpoint + '?q=' + encodeURIComponent(query);
            const response = await fetch(url, {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
                signal: controller.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.error) {
                list.replaceChildren();
                setStatus(root, payload.error || 'Не получилось выполнить поиск.', 'error');
                return;
            }

            const results = payload.results || [];
            list.replaceChildren(...results.map((result) => renderResult(root, result)));
            setStatus(
                root,
                results.length ? 'Найдено: ' + results.length + '. Нажми «Добавить», и предмет появится в каталоге.' : 'Ничего не нашлось. Попробуй другое название.',
                results.length ? '' : 'empty'
            );
        } catch (error) {
            if (error.name !== 'AbortError') {
                setStatus(root, 'Не получилось выполнить поиск.', 'error');
            }
        } finally {
            if (requests.get(root) === controller) {
                root.classList.remove('is-loading');
            }
        }
    }

    document.addEventListener('input', (event) => {
        const input = event.target.closest('[data-market-search-input]');
        if (!input) {
            return;
        }
        const root = input.closest('[data-market-search]');
        if (!root) {
            return;
        }
        window.clearTimeout(timers.get(root));
        timers.set(root, window.setTimeout(() => runSearch(root, input.value.trim()), DEBOUNCE_MS));
    });

    document.addEventListener('keydown', (event) => {
        const input = event.target.closest('[data-market-search-input]');
        if (!input || event.key !== 'Enter') {
            return;
        }
        event.preventDefault();
        const root = input.closest('[data-market-search]');
        window.clearTimeout(timers.get(root));
        runSearch(root, input.value.trim());
    });

    document.addEventListener('click', (event) => {
        const opener = event.target.closest('[data-market-search-open]');
        if (opener) {
            const dialog = document.getElementById('market-search-dialog');
            if (dialog && typeof dialog.showModal === 'function') {
                event.preventDefault();
                event.stopPropagation();
                dialog.showModal();
                const input = dialog.querySelector('[data-market-search-input]');
                if (input) {
                    input.focus();
                }
            }
            return;
        }

        const closer = event.target.closest('[data-market-search-close]');
        if (closer) {
            const dialog = closer.closest('dialog');
            if (dialog) {
                dialog.close();
            }
            return;
        }

        if (event.target.matches && event.target.matches('dialog.market-dialog')) {
            event.target.close();
        }
    }, true);
})();
