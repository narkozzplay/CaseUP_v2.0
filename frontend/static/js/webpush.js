/* Web push: subscribe this browser, the settings card and the «turn notifications on» banner. */
(function () {
    'use strict';

    const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    const SYNC_KEY = 'caseup:push-sync';

    function csrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (match) {
            return decodeURIComponent(match[1]);
        }
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    async function postJSON(url, body) {
        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrfToken() },
            body: JSON.stringify(body || {}),
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
    }

    function keyBytes(base64) {
        const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
        const raw = window.atob(padded);
        return Uint8Array.from(raw, (char) => char.charCodeAt(0));
    }

    async function currentSubscription() {
        if (!supported) {
            return null;
        }
        const registration = await navigator.serviceWorker.getRegistration('/');
        return registration ? registration.pushManager.getSubscription() : null;
    }

    /* 'unsupported' | 'ios-install' | 'denied' | 'on' | 'off' */
    async function deviceState() {
        if (!supported) {
            return isIOS && !standalone ? 'ios-install' : 'unsupported';
        }
        if (Notification.permission === 'denied') {
            return 'denied';
        }
        const subscription = await currentSubscription();
        return subscription && Notification.permission === 'granted' ? 'on' : 'off';
    }

    async function enable() {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            throw new Error(permission);
        }
        const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        await navigator.serviceWorker.ready;
        const keyResponse = await fetch('/push/key/', { credentials: 'same-origin' });
        const { publicKey } = await keyResponse.json();
        let subscription = await registration.pushManager.getSubscription();
        if (!subscription) {
            subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) });
        }
        await postJSON('/push/subscribe/', subscription.toJSON());
        rememberSync();
        return subscription;
    }

    async function disable() {
        const subscription = await currentSubscription();
        if (subscription) {
            await postJSON('/push/unsubscribe/', { endpoint: subscription.endpoint });
            await subscription.unsubscribe();
        }
    }

    function rememberSync() {
        try {
            window.localStorage.setItem(SYNC_KEY, String(Date.now()));
        } catch (error) {
            /* private mode */
        }
    }

    /* Keeps the server's list right (a browser re-subscribed, or another account signed in here). */
    async function syncOncePerDay() {
        try {
            if (Number(window.localStorage.getItem(SYNC_KEY) || 0) > Date.now() - 86400000) {
                return;
            }
        } catch (error) {
            return;
        }
        const subscription = await currentSubscription();
        if (subscription && Notification.permission === 'granted') {
            await postJSON('/push/subscribe/', subscription.toJSON());
            rememberSync();
        }
    }

    function explain(error) {
        const reason = String(error && error.message || error);
        if (reason === 'denied') {
            return 'Браузер запретил уведомления для сайта. Разрешите их в настройках сайта (значок слева от адреса) и попробуйте снова.';
        }
        if (reason === 'default') {
            return 'Вы закрыли запрос браузера. Нажмите «Включить» ещё раз и выберите «Разрешить».';
        }
        return 'Не получилось включить уведомления. Попробуйте ещё раз чуть позже.';
    }

    async function renderPanel(panel) {
        const status = panel.querySelector('[data-push-status]');
        const buttons = {
            enable: panel.querySelector('[data-push-enable]'),
            disable: panel.querySelector('[data-push-disable]'),
            test: panel.querySelector('[data-push-test]'),
        };
        const state = await deviceState();
        const texts = {
            unsupported: 'Этот браузер не поддерживает пуш-уведомления.',
            'ios-install': 'Чтобы получать уведомления на iPhone, установите сайт на экран «Домой».',
            denied: 'Уведомления запрещены в настройках браузера для этого сайта.',
            on: 'На этом устройстве уведомления включены.',
            off: 'На этом устройстве уведомления выключены.',
        };
        status.textContent = texts[state];
        status.dataset.state = state;
        buttons.enable.hidden = state !== 'off';
        buttons.disable.hidden = state !== 'on';
        buttons.test.hidden = state !== 'on';
        const ios = panel.querySelector('[data-push-ios]');
        if (ios) {
            ios.hidden = state !== 'ios-install';
        }
    }

    async function renderPrompt(prompt) {
        const state = await deviceState();
        if (state === 'on') {
            /* already on here: the server just did not know this device yet */
            const subscription = await currentSubscription();
            await postJSON('/push/subscribe/', subscription.toJSON()).catch(() => {});
            prompt.remove();
            return;
        }
        if (state === 'denied' || state === 'unsupported') {
            prompt.remove();
            return;
        }
        if (state === 'ios-install') {
            prompt.querySelector('[data-push-prompt-ios]').hidden = false;
            prompt.querySelector('[data-push-enable]').hidden = true;
            prompt.querySelector('[data-push-dismiss]').textContent = 'Понятно';
        }
        prompt.hidden = false;
    }

    function init(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-push-panel]').forEach((panel) => renderPanel(panel).catch(() => {}));
        scope.querySelectorAll('[data-push-prompt]').forEach((prompt) => renderPrompt(prompt).catch(() => prompt.remove()));
    }

    document.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-push-enable], [data-push-disable], [data-push-test], [data-push-dismiss]');
        if (!button) {
            return;
        }
        const panel = button.closest('[data-push-panel]');
        const prompt = button.closest('[data-push-prompt]');
        const status = panel ? panel.querySelector('[data-push-status]') : prompt && prompt.querySelector('[data-push-prompt-text]');
        button.disabled = true;
        try {
            if (button.matches('[data-push-enable]')) {
                await enable();
                if (prompt) {
                    prompt.querySelector('[data-push-prompt-text]').textContent = 'Готово! Уведомления включены — пришлём, как только что-то произойдёт.';
                    prompt.querySelector('.push-prompt__actions').hidden = true;
                    window.setTimeout(() => prompt.remove(), 4000);
                }
            } else if (button.matches('[data-push-disable]')) {
                await disable();
            } else if (button.matches('[data-push-test]')) {
                const result = await postJSON('/push/test/');
                status.textContent = result.sent ? 'Тестовое уведомление отправлено — оно появится через пару секунд.' : 'Не удалось доставить тестовое уведомление. Выключите и снова включите уведомления.';
                return;
            } else if (button.matches('[data-push-dismiss]')) {
                await postJSON('/push/prompt/dismiss/').catch(() => {});
                prompt.remove();
                return;
            }
            if (panel) {
                await renderPanel(panel);
            }
        } catch (error) {
            if (status) {
                status.textContent = explain(error);
            }
        } finally {
            button.disabled = false;
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        init(document);
        if (supported) {
            syncOncePerDay().catch(() => {});
        }
    });
    document.addEventListener('dashboard:content-replaced', () => init(document));
})();
