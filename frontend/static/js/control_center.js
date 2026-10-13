(function () {
    'use strict';

    document.addEventListener('change', function (event) {
        var toggle = event.target.closest('[data-cc-check-all]');
        if (!toggle) {
            return;
        }
        var form = toggle.closest('form');
        if (!form) {
            return;
        }
        form.querySelectorAll('input[name="items"]').forEach(function (box) {
            box.checked = toggle.checked;
        });
    });

    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form || !form.matches('.cc-form--danger, [data-cc-bulk]')) {
            return;
        }
        var submitter = event.submitter;
        var isDanger = form.matches('.cc-form--danger') || (submitter && submitter.value === 'disable');
        if (isDanger && !window.confirm('Подтвердите действие.')) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    /* ---------- System settings ---------- */

    function moduleScope(root) {
        var active = root.querySelector('[data-cc-module-scope] .is-active');
        return active ? active.dataset.scope : 'all';
    }

    function applyModuleFilter(root) {
        var input = root.querySelector('[data-cc-module-filter]');
        var query = input ? input.value.trim().toLowerCase() : '';
        var scope = moduleScope(root);
        var visible = 0;
        root.querySelectorAll('[data-module-row]').forEach(function (card) {
            var box = card.querySelector('input[type="checkbox"]');
            var enabled = box ? box.checked : card.dataset.enabled === '1';
            var matches = !query || (card.dataset.search || '').indexOf(query) !== -1;
            if (scope === 'on' && !enabled) { matches = false; }
            if (scope === 'off' && enabled) { matches = false; }
            card.hidden = !matches;
            if (matches) { visible += 1; }
        });
        var empty = root.querySelector('[data-cc-module-empty]');
        if (empty) { empty.hidden = visible !== 0; }
    }

    function formatNumber(value) {
        return value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
    }

    function updateCurrencyPreview(root) {
        var hint = root.querySelector('[data-cc-currency-preview]');
        var rateInput = root.querySelector('[name="site_currency_rate"]');
        var codeInput = root.querySelector('[name="site_currency_code"]');
        if (!hint || !rateInput) { return; }
        var rate = parseFloat(String(rateInput.value).replace(',', '.'));
        var code = codeInput ? codeInput.value : '';
        var rub = parseFloat(hint.dataset.rub || '1000');
        if (!rate || rate <= 0) {
            hint.textContent = 'Курс должен быть больше нуля.';
            return;
        }
        hint.innerHTML = '';
        hint.append(formatNumber(rub) + ' RUB на балансе покажутся как ');
        var strong = document.createElement('b');
        strong.textContent = formatNumber(rub / rate) + ' ' + code;
        hint.append(strong);
    }

    function setDirty(form, dirty) {
        var bar = form.querySelector('[data-cc-savebar]');
        var label = form.querySelector('[data-cc-dirty-label]');
        if (!bar) { return; }
        bar.classList.toggle('is-dirty', dirty);
        if (label) { label.textContent = dirty ? 'Есть несохранённые изменения' : 'Изменений нет'; }
    }

    function initSettings(scope) {
        var root = (scope && scope.querySelector) ? scope : document;
        if (!root.querySelector('.cc-settings')) { return; }
        updateCurrencyPreview(root);
        applyModuleFilter(root);
    }

    document.addEventListener('input', function (event) {
        var target = event.target;
        var section = target.closest('.cc-settings');
        if (!section) { return; }
        if (target.matches('[data-cc-module-filter]')) {
            applyModuleFilter(section);
            return;
        }
        if (target.matches('[name="site_currency_rate"]')) { updateCurrencyPreview(section); }
        var form = target.closest('[data-cc-dirty-form]');
        if (form) { setDirty(form, true); }
    });

    document.addEventListener('change', function (event) {
        var target = event.target;
        var section = target.closest('.cc-settings');
        if (!section) { return; }
        if (target.matches('[name="site_currency_code"]')) { updateCurrencyPreview(section); }
        if (target.matches('[data-module-row] input[type="checkbox"]')) {
            var card = target.closest('[data-module-row]');
            card.classList.toggle('is-on', target.checked);
        }
        var form = target.closest('[data-cc-dirty-form]');
        if (form && !target.matches('[data-cc-module-filter]')) { setDirty(form, true); }
    });

    document.addEventListener('click', function (event) {
        var reveal = event.target.closest('[data-cc-reveal]');
        if (reveal) {
            var input = reveal.parentElement.querySelector('input');
            if (input) {
                var show = input.type === 'password';
                input.type = show ? 'text' : 'password';
                reveal.classList.toggle('is-on', show);
                reveal.setAttribute('aria-label', show ? 'Скрыть ключ' : 'Показать ключ');
            }
            return;
        }
        var scopeButton = event.target.closest('[data-cc-module-scope] [data-scope]');
        if (scopeButton) {
            scopeButton.parentElement.querySelectorAll('[data-scope]').forEach(function (button) {
                button.classList.toggle('is-active', button === scopeButton);
            });
            applyModuleFilter(scopeButton.closest('.cc-settings'));
            return;
        }
        var preview = event.target.closest('[data-cc-maintenance-preview]');
        if (preview) {
            var section = preview.closest('.cc-settings');
            var message = section && section.querySelector('[name="maintenance_message"]');
            if (message && message.value.trim()) {
                var url = new URL(preview.href, window.location.origin);
                url.searchParams.set('message', message.value.trim());
                preview.href = url.toString();
            }
        }
    });

    document.addEventListener('reset', function (event) {
        var form = event.target;
        if (!form.matches || !form.matches('[data-cc-dirty-form]')) { return; }
        window.setTimeout(function () {
            setDirty(form, false);
            form.querySelectorAll('[data-module-row]').forEach(function (card) {
                var box = card.querySelector('input[type="checkbox"]');
                if (box) { card.classList.toggle('is-on', box.checked); }
            });
            var section = form.closest('.cc-settings');
            if (section) {
                updateCurrencyPreview(section);
                applyModuleFilter(section);
            }
        }, 0);
    });

    document.addEventListener('dashboard:content-replaced', function (event) {
        initSettings(event.detail && event.detail.frame);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { initSettings(document); });
    } else {
        initSettings(document);
    }
})();
