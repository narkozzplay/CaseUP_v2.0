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

/* ---------- Price auto-update: live schedule preview ---------- */
(function () {
    'use strict';

    function describe(form) {
        var mode = form.querySelector('[name="schedule_mode"]');
        var enabled = form.querySelector('[name="is_enabled"]');
        var category = form.querySelector('[name="category"]');
        if (!mode) {
            return '';
        }
        var what = category && category.value
            ? 'Цены «' + category.options[category.selectedIndex].text + '»'
            : 'Цены';
        if (enabled && !enabled.checked) {
            return what + ' не будут обновляться автоматически, пока правило выключено.';
        }
        if (mode.value === 'hourly') {
            var minuteField = form.querySelector('[name="hourly_minute"]');
            var minute = parseInt(minuteField && minuteField.value, 10);
            if (isNaN(minute) || minute < 0 || minute > 59) {
                return 'Укажите минуту от 0 до 59.';
            }
            var mm = (minute < 10 ? '0' : '') + minute;
            return what + ' будут обновляться каждый час в :' + mm + ' (например, 10:' + mm + ', 11:' + mm + ').';
        }
        var interval = form.querySelector('[name="interval_minutes"]');
        var start = form.querySelector('[name="start_at"]');
        var text = what + ' будут обновляться каждые ' + (interval ? interval.value : '?') + ' мин';
        if (start && start.value) {
            text += ', первый запуск ' + start.value.replace('T', ' в ');
        } else {
            text += ', первый запуск сразу после сохранения';
        }
        return text + '.';
    }

    function refresh(root) {
        (root || document).querySelectorAll('[data-autoprice-form]').forEach(function (form) {
            var preview = form.querySelector('[data-autoprice-preview]');
            if (preview) {
                preview.textContent = describe(form);
            }
        });
    }

    ['input', 'change'].forEach(function (type) {
        document.addEventListener(type, function (event) {
            var form = event.target.closest && event.target.closest('[data-autoprice-form]');
            if (form) {
                refresh(form.parentNode);
            }
        });
    });
    document.addEventListener('DOMContentLoaded', function () { refresh(document); });
    document.addEventListener('dashboard:content-replaced', function () { refresh(document); });
})();

// Control Center → Промокоды: show only the fields that belong to the chosen kind.
(function () {
    'use strict';

    function sync(form) {
        var select = form.querySelector('select[name="kind"]');
        if (!select) {
            return;
        }
        form.querySelectorAll('[data-promo-kinds]').forEach(function (field) {
            var kinds = field.getAttribute('data-promo-kinds').split(' ');
            field.hidden = kinds.indexOf(select.value) === -1;
        });
    }

    function init(scope) {
        (scope || document).querySelectorAll('[data-promo-form]').forEach(sync);
    }

    document.addEventListener('change', function (event) {
        if (event.target.matches('[data-promo-form] select[name="kind"]')) {
            sync(event.target.closest('[data-promo-form]'));
        }
    });
    document.addEventListener('dashboard:content-replaced', function () { init(document); });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(document); });
    } else {
        init(document);
    }
})();

/* ---------- Donations: live preview of the widget while the form is edited ---------- */
(function () {
    'use strict';

    var CLASS_PREFIX = { variant: 'donation--', background: 'donation--bg-' };
    var CHOICES = { variant: ['card', 'banner', 'compact'], background: ['theme', 'tint', 'gradient'] };

    function setText(widget, key, value) {
        var node = widget.querySelector('[data-donation-preview="' + key + '"]');
        if (node) {
            node.textContent = value;
        }
    }

    function toggle(widget, selector, visible) {
        var node = widget.querySelector(selector);
        if (node) {
            node.hidden = !visible;
        }
    }

    document.addEventListener('input', update);
    document.addEventListener('change', update);

    function update(event) {
        var field = event.target.closest('[data-donation-field]');
        var form = field && field.closest('[data-donation-form]');
        var root = form && form.closest('[data-control-center]');
        var widget = root && root.querySelector('[data-donation-preview-root] [data-donation-widget]');
        if (!widget) {
            return;
        }
        var name = field.dataset.donationField;
        var value = field.type === 'checkbox' ? field.checked : field.value;
        if (CHOICES[name]) {
            CHOICES[name].forEach(function (choice) {
                widget.classList.toggle(CLASS_PREFIX[name] + choice, choice === value);
            });
        } else if (name === 'accent_color' || name === 'accent_color_end') {
            widget.style.setProperty(name === 'accent_color' ? '--donation-accent' : '--donation-accent-end', value);
        } else if (name === 'title' || name === 'description' || name === 'button_text' || name === 'icon') {
            setText(widget, name, value);
        } else if (name === 'show_percent') {
            toggle(widget, '.donation__percent', value);
        } else if (name === 'show_donors_count') {
            toggle(widget, '.donation__donors', value);
        }
    }
})();
