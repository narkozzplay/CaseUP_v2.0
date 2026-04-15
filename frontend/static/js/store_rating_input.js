(function () {
    var controls = document.querySelectorAll('[data-store-rating-input]');
    if (!controls.length) {
        return;
    }

    controls.forEach(function (control) {
        var input = control.querySelector('[data-store-rating-value]');
        var buttons = Array.prototype.slice.call(control.querySelectorAll('[data-store-rating-button]'));
        if (!input || !buttons.length) {
            return;
        }

        var fullIcon = control.getAttribute('data-star-full-icon') || '';
        var emptyIcon = control.getAttribute('data-star-empty-icon') || '';

        var applyVisualState = function (activeValue) {
            buttons.forEach(function (button) {
                var value = parseInt(button.getAttribute('data-store-rating-button'), 10) || 0;
                var icon = button.querySelector('img[data-store-rating-icon]');
                var isActive = value <= activeValue;
                button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                if (icon) {
                    icon.src = isActive ? fullIcon : emptyIcon;
                }
            });
        };

        var currentValue = parseInt(input.value, 10) || 5;
        if (currentValue < 1 || currentValue > 5) {
            currentValue = 5;
            input.value = '5';
        }
        applyVisualState(currentValue);

        buttons.forEach(function (button) {
            var value = parseInt(button.getAttribute('data-store-rating-button'), 10) || 0;
            button.addEventListener('mouseenter', function () {
                applyVisualState(value);
            });
            button.addEventListener('click', function () {
                currentValue = value;
                input.value = String(value);
                applyVisualState(currentValue);
            });
            button.addEventListener('focus', function () {
                applyVisualState(value);
            });
        });

        control.addEventListener('mouseleave', function () {
            applyVisualState(currentValue);
        });
    });
})();
