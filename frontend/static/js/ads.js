// Renders ad blocks placed by {% ad_slot %}; runs again after the dashboard swaps page content.
(function () {
    'use strict';

    var counter = 0;

    function renderYandex(node) {
        var blockId = node.getAttribute('data-ad-block');
        if (!blockId) return;
        counter += 1;
        var renderTo = 'yandex_rtb_' + blockId + '_' + counter;
        node.id = renderTo;
        window.yaContextCb = window.yaContextCb || [];
        window.yaContextCb.push(function () {
            if (window.Ya && window.Ya.Context && window.Ya.Context.AdvManager) {
                window.Ya.Context.AdvManager.render({ blockId: blockId, renderTo: renderTo });
            }
        });
    }

    function renderGoogle() {
        try {
            (window.adsbygoogle = window.adsbygoogle || []).push({});
        } catch (error) {
            // The network script failed to load (ad blocker); leave the slot empty.
        }
    }

    function renderAll(root) {
        var scope = root || document;
        var nodes = scope.querySelectorAll('[data-caseup-ad]:not([data-ad-rendered])');
        Array.prototype.forEach.call(nodes, function (node) {
            node.setAttribute('data-ad-rendered', '1');
            if (node.getAttribute('data-caseup-ad') === 'google') {
                renderGoogle();
            } else {
                renderYandex(node);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { renderAll(document); });
    } else {
        renderAll(document);
    }
    document.addEventListener('dashboard:content-replaced', function (event) {
        renderAll((event.detail && event.detail.frame) || document);
    });
})();
