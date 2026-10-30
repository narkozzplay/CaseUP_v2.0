// Control Center → «Сервер»: the forecast under «Баланс пополнен» while the amount is typed.
(function () {
    'use strict';

    function fmt(value) {
        return value.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function update(form) {
        const input = form.querySelector('[data-srv-topup-amount]');
        const out = form.querySelector('[data-srv-topup-preview]');
        const balance = parseFloat(form.dataset.balance) || 0;
        const hourly = parseFloat(form.dataset.hourly) || 0;
        const reserveDays = parseInt(form.dataset.reserveDays, 10) || 0;
        const amount = parseFloat(input && input.value);
        if (!out || !hourly || !(amount > 0)) {
            return;
        }
        const end = new Date(Date.now() + ((balance + amount) / hourly) * 3600 * 1000);
        const renew = new Date(end.getTime() - reserveDays * 86400 * 1000);
        out.innerHTML = `После пополнения: баланс ${fmt(balance + amount)} ₽, хватит до ${end.toLocaleDateString('ru-RU')}. `
            + `Следующее пополнение — до <b>${renew.toLocaleDateString('ru-RU')}</b>.`;
    }

    document.addEventListener('input', (event) => {
        const form = event.target.closest && event.target.closest('[data-srv-topup]');
        if (form) {
            update(form);
        }
    });
})();
