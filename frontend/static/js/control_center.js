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
})();
