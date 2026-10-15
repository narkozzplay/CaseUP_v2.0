// Copy-to-clipboard buttons: <button data-copy-target="#input-id" data-copy-done="Скопировано">.
(function () {
    'use strict';

    function fallbackCopy(field) {
        field.focus();
        field.select();
        try {
            return document.execCommand('copy');
        } catch (error) {
            return false;
        }
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-copy-target]');
        if (!button) {
            return;
        }
        var field = document.querySelector(button.getAttribute('data-copy-target'));
        if (!field) {
            return;
        }
        event.preventDefault();
        var label = button.querySelector('[data-copy-label]') || button;
        var original = label.textContent;
        var done = function () {
            label.textContent = button.getAttribute('data-copy-done') || 'Скопировано';
            button.classList.add('is-copied');
            window.setTimeout(function () {
                label.textContent = original;
                button.classList.remove('is-copied');
            }, 1800);
        };
        var text = field.value || field.textContent || '';
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, function () {
                if (fallbackCopy(field)) { done(); }
            });
        } else if (fallbackCopy(field)) {
            done();
        }
    });
})();
