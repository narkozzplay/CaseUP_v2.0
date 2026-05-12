(function () {
    function clampRating(value) {
        var parsed = parseInt(value, 10);
        if (isNaN(parsed)) {
            return 0;
        }
        if (parsed < 0) {
            return 0;
        }
        if (parsed > 5) {
            return 5;
        }
        return parsed;
    }

    function initStoreRatingInputs(scope) {
        var root = scope && scope.querySelector ? scope : document;
        var controls = root.querySelectorAll('[data-store-rating-input]');

        controls.forEach(function (control) {
            if (control.dataset.storeRatingInitialized === '1') {
                return;
            }

            var input = control.querySelector('[data-store-rating-value]');
            var buttons = Array.prototype.slice.call(control.querySelectorAll('[data-store-rating-button]'));
            if (!input || !buttons.length) {
                return;
            }

            var fullIcon = control.getAttribute('data-star-full-icon') || '';
            var emptyIcon = control.getAttribute('data-star-empty-icon') || '';

            function applyVisualState(activeValue) {
                var normalized = clampRating(activeValue);
                buttons.forEach(function (button) {
                    var value = clampRating(button.getAttribute('data-store-rating-button'));
                    var icon = button.querySelector('img[data-store-rating-icon]');
                    var isActive = value > 0 && value <= normalized;
                    button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                    if (icon) {
                        icon.src = isActive ? fullIcon : emptyIcon;
                    }
                });
            }

            var currentValue = clampRating(input.value);
            input.value = String(currentValue);
            applyVisualState(currentValue);

            buttons.forEach(function (button) {
                var value = clampRating(button.getAttribute('data-store-rating-button'));

                button.addEventListener('mouseenter', function () {
                    applyVisualState(value);
                });

                button.addEventListener('focus', function () {
                    applyVisualState(value);
                });

                button.addEventListener('click', function () {
                    currentValue = value;
                    input.value = String(value);
                    applyVisualState(currentValue);
                });

                button.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        currentValue = value;
                        input.value = String(value);
                        applyVisualState(currentValue);
                    }
                });
            });

            control.addEventListener('mouseleave', function () {
                applyVisualState(currentValue);
            });

            control.addEventListener('focusout', function (event) {
                if (control.contains(event.relatedTarget)) {
                    return;
                }
                applyVisualState(currentValue);
            });

            control.dataset.storeRatingInitialized = '1';
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initStoreRatingInputs(document);
        });
    } else {
        initStoreRatingInputs(document);
    }

    document.addEventListener('dashboard:content-replaced', function (event) {
        var scope = event && event.detail && event.detail.frame ? event.detail.frame : document;
        initStoreRatingInputs(scope);
    });

    window.CaseUPStoreRatingInput = {
        init: initStoreRatingInputs
    };
})();
