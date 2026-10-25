// Cookie consent bar: "Согласен" stores the consent cookies it lists (analytics, ads) and reloads so the scripts load.
(function () {
    'use strict';

    var DECLINED_KEY = 'caseup_cookie_consent_declined';

    function bind() {
        var bar = document.querySelector('[data-cookie-consent]');
        if (!bar) return;
        try {
            if (window.sessionStorage.getItem(DECLINED_KEY) === '1') {
                bar.hidden = true;
                return;
            }
        } catch (error) {
            // Storage may be blocked; the bar just stays visible.
        }
        bar.querySelector('[data-cookie-consent-accept]').addEventListener('click', function () {
            (bar.getAttribute('data-consent-cookies') || '').split(/\s+/).forEach(function (name) {
                if (name) document.cookie = name + '=1; max-age=31536000; path=/; SameSite=Lax';
            });
            window.location.reload();
        });
        bar.querySelector('[data-cookie-consent-decline]').addEventListener('click', function () {
            try { window.sessionStorage.setItem(DECLINED_KEY, '1'); } catch (error) { /* ignore */ }
            bar.hidden = true;
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
