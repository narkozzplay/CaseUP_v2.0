/* Steam catalog: favorite stars without a page reload, filters that apply on change. */
(function () {
    'use strict';

    document.addEventListener('submit', async (event) => {
        const form = event.target.closest('form[data-catalog-fav]');
        if (!form) {
            return;
        }
        event.preventDefault();
        const button = form.querySelector('.catalog-fav__btn');
        if (button.disabled) {
            return;
        }
        button.disabled = true;
        try {
            const response = await fetch(form.action, {
                method: 'POST',
                body: new window.FormData(form),
                headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
            });
            if (!response.ok) {
                throw new Error(String(response.status));
            }
            const payload = await response.json();
            button.classList.toggle('is-active', payload.favorite);
            button.setAttribute('aria-pressed', payload.favorite ? 'true' : 'false');
            button.title = payload.favorite ? 'Убрать из избранного' : 'В избранное';
            document.querySelectorAll('.chip-tab--fav .chip-tab__num').forEach((node) => {
                node.textContent = payload.count;
            });
            const onlyFavorites = new URLSearchParams(window.location.search).get('fav') === '1';
            const card = form.closest('.catalog-card');
            if (card && onlyFavorites && !payload.favorite) {
                card.remove();
            }
        } catch (error) {
            form.submit();
        } finally {
            button.disabled = false;
        }
    });

    document.addEventListener('change', (event) => {
        const field = event.target.closest('[data-autosubmit]');
        if (!field || !field.form) {
            return;
        }
        if (field.form.requestSubmit) {
            field.form.requestSubmit();
        } else {
            field.form.submit();
        }
    });
})();
