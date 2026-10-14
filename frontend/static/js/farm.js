(function () {
    'use strict';

    var STEAM_ID64_BASE = BigInt('76561197960265728');

    function money(value) {
        return value.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽';
    }

    function parsePrice(raw) {
        var value = parseFloat(String(raw || '').replace(/\s/g, '').replace(',', '.'));
        return isNaN(value) || value < 0 ? 0 : value;
    }

    function escapeHtml(text) {
        var node = document.createElement('span');
        node.textContent = text == null ? '' : String(text);
        return node.innerHTML;
    }

    /* ---------- Connect: live trade link check ---------- */

    function parseTradeUrl(value) {
        var result = { partner: '', token: '', steamId: '' };
        var match = String(value || '').match(/[?&]partner=(\d+)/);
        if (match) {
            result.partner = match[1];
            try {
                result.steamId = (STEAM_ID64_BASE + BigInt(match[1])).toString();
            } catch (error) {
                result.steamId = '';
            }
        }
        var token = String(value || '').match(/[?&]token=([\w-]+)/);
        if (token) {
            result.token = token[1];
        }
        return result;
    }

    function setCheck(root, code, state, hint) {
        var node = root.querySelector('[data-farm-preview-check="' + code + '"]');
        if (!node) {
            return;
        }
        node.className = 'fm-check fm-check--' + state;
        node.querySelector('.fm-check__dot').textContent = state === 'ok' ? '✓' : state === 'fail' ? '!' : '·';
        if (hint) {
            node.querySelector('.fm-check__text span').textContent = hint;
        }
    }

    function updateTradePreview(form) {
        var root = form.closest('[data-farm-connect]');
        var preview = root && root.querySelector('[data-farm-trade-preview]');
        if (!preview) {
            return;
        }
        var input = form.querySelector('[data-farm-trade-input]');
        var titleInput = form.querySelector('input[name="title"]');
        var value = input ? input.value.trim() : '';
        var parsed = parseTradeUrl(value);
        var title = titleInput && titleInput.value.trim();

        preview.querySelector('[data-farm-preview-title]').textContent = title || 'Новый аккаунт';
        var digits = (title || '').match(/(\d{1,3})\s*$/);
        preview.querySelector('[data-farm-preview-initials]').textContent = digits ? digits[1].slice(-2) : (title || '?').slice(0, 2).toUpperCase();
        var steamNode = preview.querySelector('[data-farm-preview-steamid]');
        var profile = preview.querySelector('[data-farm-preview-profile]');

        if (!value) {
            steamNode.textContent = 'SteamID появится после вставки ссылки';
            setCheck(preview, 'partner', 'idle', 'из параметра partner');
            setCheck(preview, 'token', 'idle', 'нужен, чтобы отправлять трейды');
            profile.hidden = true;
            return;
        }
        if (parsed.steamId) {
            steamNode.textContent = 'SteamID64 ' + parsed.steamId;
            setCheck(preview, 'partner', 'ok', 'partner ' + parsed.partner);
            profile.href = 'https://steamcommunity.com/profiles/' + parsed.steamId;
            profile.hidden = false;
        } else {
            steamNode.textContent = 'Не похоже на трейд-ссылку Steam';
            setCheck(preview, 'partner', 'fail', 'в ссылке нет partner');
            profile.hidden = true;
        }
        setCheck(preview, 'token', parsed.token ? 'ok' : 'fail', parsed.token ? 'есть' : 'в ссылке нет token');
    }

    function updateImportCount(textarea) {
        var root = textarea.closest('[data-farm-import]');
        var counter = root && root.querySelector('[data-farm-import-count]');
        if (!counter) {
            return;
        }
        var lines = textarea.value.split(/\r?\n/).filter(function (line) { return line.trim(); });
        var valid = lines.filter(function (line) { return /steamcommunity\.com\/tradeoffer\/new\/\S*partner=\d+/i.test(line); });
        counter.textContent = lines.length ? 'Строк: ' + lines.length + ' · со ссылкой: ' + valid.length : 'до 100 строк';
    }

    /* ---------- Drop entry ---------- */

    function dropForm(node) {
        return node && node.closest('[data-farm-drop-form]');
    }

    function selectedTiles(form) {
        var seen = {};
        var tiles = [];
        form.querySelectorAll('[data-farm-item]').forEach(function (tile) {
            var box = tile.querySelector('.fm-item__check');
            var id = tile.dataset.itemId;
            if (box && box.checked && !seen[id]) {
                seen[id] = true;
                tiles.push(tile);
            }
        });
        return tiles;
    }

    function syncTwins(form, id, source) {
        // The same item can be shown in the pool, "frequent" and search results.
        form.querySelectorAll('[data-farm-item][data-item-id="' + id + '"]').forEach(function (tile) {
            if (tile === source) {
                return;
            }
            var box = tile.querySelector('.fm-item__check');
            box.checked = false;
            tile.classList.remove('is-selected');
        });
    }

    function renderSummary(form) {
        var list = form.querySelector('[data-farm-summary]');
        var totalNode = form.querySelector('[data-farm-total]');
        if (!list || !totalNode) {
            return;
        }
        var rows = [];
        var total = 0;
        selectedTiles(form).forEach(function (tile) {
            var qty = parseInt(tile.querySelector('[data-farm-qty-input]').value, 10) || 1;
            var price = parsePrice(tile.querySelector('[data-farm-price-input]').value);
            total += qty * price;
            rows.push({ name: tile.querySelector('.fm-item__name').textContent, qty: qty, sum: qty * price });
        });
        form.querySelectorAll('[data-farm-manual] .fm-manual__row').forEach(function (row) {
            var inputs = row.querySelectorAll('input');
            var name = inputs[0].value.trim();
            if (!name) {
                return;
            }
            var qty = parseInt(inputs[1].value, 10) || 1;
            var price = parsePrice(inputs[2].value);
            total += qty * price;
            rows.push({ name: name, qty: qty, sum: qty * price });
        });

        list.querySelectorAll('.fm-summary__row').forEach(function (row) { row.remove(); });
        var empty = list.querySelector('[data-farm-summary-empty]');
        if (empty) {
            empty.hidden = rows.length > 0;
        }
        rows.forEach(function (row) {
            var node = document.createElement('div');
            node.className = 'fm-summary__row';
            node.innerHTML = '<span>' + escapeHtml(row.name) + '</span><b>×' + row.qty + '</b><em>' + money(row.sum) + '</em>';
            list.appendChild(node);
        });
        totalNode.textContent = rows.length ? money(total) : '—';

        var max = parseInt(form.dataset.maxItems || '12', 10);
        list.classList.toggle('is-over', rows.length > max);
    }

    function tileHtml(item) {
        var icon = item.icon ? '<img src="' + escapeHtml(item.icon) + '" alt="" loading="lazy">' : '';
        var price = item.price ? ' · ' + money(parsePrice(item.price)) : '';
        return '<label class="fm-item is-found" data-farm-item data-item-id="' + item.id + '">' +
            '<input type="checkbox" name="item" value="' + item.id + '" class="fm-item__check">' +
            '<span class="fm-item__media">' + icon + '</span>' +
            '<span class="fm-item__name">' + escapeHtml(item.name) + '</span>' +
            '<span class="fm-item__meta">' + escapeHtml(item.type) + price + '</span>' +
            '<span class="fm-item__mark" aria-hidden="true">✓</span>' +
            '<span class="fm-item__fields">' +
            '<span class="fm-qty"><button type="button" class="fm-qty__btn" data-farm-qty="-1" aria-label="Меньше">−</button>' +
            '<input type="number" name="qty_' + item.id + '" value="1" min="1" max="999" class="fm-qty__input" aria-label="Количество" data-farm-qty-input>' +
            '<button type="button" class="fm-qty__btn" data-farm-qty="1" aria-label="Больше">+</button></span>' +
            '<span class="fm-price"><input type="text" inputmode="decimal" name="price_' + item.id + '" value="' + escapeHtml(item.price) + '" class="fm-price__input" aria-label="Цена за штуку" data-farm-price-input><span>₽</span></span>' +
            '</span></label>';
    }

    var searchTimer = null;
    var searchSeq = 0;

    function runItemSearch(input) {
        var form = dropForm(input);
        var results = form.querySelector('[data-farm-search-results]');
        var status = form.querySelector('[data-farm-search-status]');
        var query = input.value.trim();

        // Keep tiles the user already checked, drop the rest of old results.
        results.querySelectorAll('.fm-item.is-found').forEach(function (tile) {
            if (!tile.querySelector('.fm-item__check').checked) {
                tile.remove();
            }
        });
        if (query.length < 2) {
            status.textContent = '';
            return;
        }
        var seq = ++searchSeq;
        status.textContent = 'Ищу…';
        fetch(form.dataset.searchUrl + '?q=' + encodeURIComponent(query), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (response) { return response.json(); })
            .then(function (payload) {
                if (seq !== searchSeq) {
                    return;
                }
                var shown = 0;
                (payload.results || []).forEach(function (item) {
                    if (form.querySelector('[data-farm-item][data-item-id="' + item.id + '"]')) {
                        return;
                    }
                    results.insertAdjacentHTML('beforeend', tileHtml(item));
                    shown += 1;
                });
                var total = (payload.results || []).length;
                status.textContent = total ? 'Найдено: ' + total + (shown < total ? ' (часть уже ниже в пуле)' : '') : 'Ничего не нашлось, добавь вручную ниже.';
            })
            .catch(function () {
                if (seq === searchSeq) {
                    status.textContent = 'Поиск недоступен, попробуй ещё раз.';
                }
            });
    }

    function filterAccounts(input) {
        var query = input.value.trim().toLowerCase();
        var form = dropForm(input);
        form.querySelectorAll('[data-farm-account]').forEach(function (node) {
            node.hidden = query && node.dataset.accountName.indexOf(query) === -1 && !node.querySelector('input').checked;
        });
    }

    /* ---------- Delegated events ---------- */

    document.addEventListener('input', function (event) {
        var target = event.target;
        if (target.matches('[data-farm-trade-input], [data-farm-connect-form] input[name="title"]')) {
            updateTradePreview(target.form);
        } else if (target.matches('[data-farm-import-input]')) {
            updateImportCount(target);
        } else if (target.matches('[data-farm-item-search]')) {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function () { runItemSearch(target); }, 250);
        } else if (target.matches('[data-farm-account-filter]')) {
            filterAccounts(target);
        } else if (dropForm(target)) {
            renderSummary(dropForm(target));
        }
    });

    document.addEventListener('change', function (event) {
        var target = event.target;
        if (target.matches('.fm-item__check')) {
            var tile = target.closest('[data-farm-item]');
            var form = dropForm(target);
            tile.classList.toggle('is-selected', target.checked);
            if (target.checked) {
                syncTwins(form, tile.dataset.itemId, tile);
            }
            renderSummary(form);
        } else if (target.matches('.fm-role input, .farm-choice__input')) {
            var group = target.closest('.fm-roles');
            if (group) {
                group.querySelectorAll('.fm-role').forEach(function (node) {
                    node.classList.toggle('is-selected', node.querySelector('input').checked);
                });
            }
        }
    });

    document.addEventListener('keydown', function (event) {
        // Enter in the catalog search must not submit the drop form.
        if (event.key === 'Enter' && event.target.matches('[data-farm-item-search], [data-farm-account-filter]')) {
            event.preventDefault();
        }
    });

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-farm-qty]');
        if (!button) {
            return;
        }
        event.preventDefault();
        var input = button.parentNode.querySelector('[data-farm-qty-input]');
        var value = (parseInt(input.value, 10) || 1) + parseInt(button.dataset.farmQty, 10);
        input.value = Math.min(Math.max(value, 1), 999);
        var tile = button.closest('[data-farm-item]');
        var box = tile && tile.querySelector('.fm-item__check');
        if (box && !box.checked) {
            box.checked = true;
            tile.classList.add('is-selected');
            syncTwins(dropForm(tile), tile.dataset.itemId, tile);
        }
        renderSummary(dropForm(button));
    });

    document.addEventListener('submit', function (event) {
        var form = event.target;
        var message = form && form.dataset && form.dataset.farmConfirm;
        if (message && !window.confirm(message)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    function init(root) {
        (root || document).querySelectorAll('[data-farm-drop-form]').forEach(renderSummary);
        (root || document).querySelectorAll('[data-farm-connect-form]').forEach(updateTradePreview);
        (root || document).querySelectorAll('[data-farm-import-input]').forEach(updateImportCount);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(document); });
    } else {
        init(document);
    }
    document.addEventListener('dashboard:content-replaced', function (event) {
        init((event.detail && event.detail.frame) || document);
    });
})();
