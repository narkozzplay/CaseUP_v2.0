(function () {
    const shell = document.querySelector('[data-dashboard-shell]');
    if (!shell) {
        return;
    }

    const contentRoot = document.querySelector('[data-dashboard-content-root]');
    const sidebar = document.querySelector('[data-dashboard-sidebar]');
    const partialHeaderName = 'X-Dashboard-Partial';
    const partialHeaderValue = '1';
    const currentUrlHeaderName = 'X-Dashboard-Current-Url';
    const themeModeHeaderName = 'X-Dashboard-Theme-Mode';
    const themeCssUrlHeaderName = 'X-Dashboard-Theme-Css-Url';
    const themeKeyHeaderName = 'X-Dashboard-Theme-Key';
    const reloadUrlHeaderName = 'X-Dashboard-Reload-Url';
    const openStateStorageKey = 'caseup.dashboard.sidebar.groups';
    const rightSocialSidebarStorageKey = 'caseup:right-social-sidebar:collapsed';
    const rightSocialSidebarMobileQuery = window.matchMedia('(max-width: 960px)');

    function isModifiedClick(event) {
        return event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0;
    }

    function shouldHandleAnchor(anchor) {
        if (!anchor || !shell.contains(anchor)) {
            return false;
        }
        if (anchor.dataset.dashboardNav === 'false' || anchor.hasAttribute('download')) {
            return false;
        }
        if (anchor.target && anchor.target !== '_self') {
            return false;
        }
        const href = anchor.getAttribute('href') || '';
        if (!href || href.startsWith('#')) {
            return false;
        }
        const url = new URL(anchor.href, window.location.origin);
        return url.origin === window.location.origin;
    }

    function syncSidebarActiveState(activeKey, href) {
        if (!sidebar) {
            return;
        }
        sidebar.querySelectorAll('[data-menu-key]').forEach((node) => {
            const isActive = Boolean(activeKey) ? node.dataset.menuKey === activeKey : node.href === href;
            node.classList.toggle('is-active', isActive);
            if (isActive) {
                node.setAttribute('aria-current', 'page');
                const group = node.closest('[data-dashboard-group]');
                if (group) {
                    group.open = true;
                }
            } else {
                node.removeAttribute('aria-current');
            }
        });
    }

    function syncSocialActiveState(activeKey, href) {
        shell.querySelectorAll('[data-social-menu-key]').forEach((node) => {
            const isActive = Boolean(activeKey) ? node.dataset.socialMenuKey === activeKey : node.href === href;
            node.classList.toggle('is-active', isActive);
            if (isActive) {
                node.setAttribute('aria-current', 'page');
            } else {
                node.removeAttribute('aria-current');
            }
        });
    }

    function syncTitleAndActiveStateFromContent() {
        const frame = contentRoot.querySelector('.dashboard-content-frame');
        if (!frame) {
            return;
        }
        const pageTitle = frame.dataset.dashboardPageTitle;
        const activeMenu = frame.dataset.dashboardActiveMenu;
        if (pageTitle) {
            document.title = pageTitle;
        }
        syncSidebarActiveState(activeMenu, window.location.href);
        syncSocialActiveState(activeMenu, window.location.href);
        document.dispatchEvent(new CustomEvent('dashboard:content-replaced', { detail: { frame } }));
    }

    function readRightSocialSidebarCollapsed() {
        const stored = window.localStorage.getItem(rightSocialSidebarStorageKey);
        if (stored === 'true') {
            return true;
        }
        if (stored === 'false') {
            return false;
        }
        return rightSocialSidebarMobileQuery.matches;
    }

    function setRightSocialSidebarCollapsed(collapsed, { persist = true } = {}) {
        const isCollapsed = Boolean(collapsed);
        shell.classList.toggle('is-social-sidebar-collapsed', isCollapsed);
        shell.classList.toggle('is-social-sidebar-mobile-open', !isCollapsed && rightSocialSidebarMobileQuery.matches);
        shell.querySelectorAll('[data-right-social-sidebar-toggle]').forEach((button) => {
            button.setAttribute('aria-expanded', String(!isCollapsed));
        });
        if (persist) {
            window.localStorage.setItem(rightSocialSidebarStorageKey, String(isCollapsed));
        }
    }

    function initRightSocialSidebar() {
        const sidebarNode = shell.querySelector('[data-right-social-sidebar]');
        if (!sidebarNode) {
            return;
        }

        setRightSocialSidebarCollapsed(readRightSocialSidebarCollapsed(), { persist: false });
        shell.querySelectorAll('[data-right-social-sidebar-toggle]').forEach((button) => {
            if (button.dataset.dashboardInitBound === 'true') {
                return;
            }
            button.dataset.dashboardInitBound = 'true';
            button.addEventListener('click', () => {
                setRightSocialSidebarCollapsed(!shell.classList.contains('is-social-sidebar-collapsed'));
            });
        });

        shell.querySelectorAll('[data-right-social-sidebar-backdrop]').forEach((backdrop) => {
            if (backdrop.dataset.dashboardInitBound === 'true') {
                return;
            }
            backdrop.dataset.dashboardInitBound = 'true';
            backdrop.addEventListener('click', () => {
                setRightSocialSidebarCollapsed(true);
            });
        });

        rightSocialSidebarMobileQuery.addEventListener('change', () => {
            setRightSocialSidebarCollapsed(readRightSocialSidebarCollapsed(), { persist: false });
        });
    }

    function persistGroupState() {
        if (!sidebar) {
            return;
        }
        const state = {};
        sidebar.querySelectorAll('[data-dashboard-group]').forEach((group) => {
            state[group.dataset.dashboardGroup] = group.open;
        });
        window.localStorage.setItem(openStateStorageKey, JSON.stringify(state));
    }

    function restoreGroupState() {
        if (!sidebar) {
            return;
        }
        let state = {};
        try {
            state = JSON.parse(window.localStorage.getItem(openStateStorageKey) || '{}');
        } catch (error) {
            state = {};
        }
        sidebar.querySelectorAll('[data-dashboard-group]').forEach((group) => {
            if (Object.prototype.hasOwnProperty.call(state, group.dataset.dashboardGroup)) {
                group.open = Boolean(state[group.dataset.dashboardGroup]);
            }
        });
    }

    async function fetchPartial(url, options) {
        const response = await window.fetch(url, {
            ...options,
            headers: {
                [partialHeaderName]: partialHeaderValue,
                'X-Requested-With': 'XMLHttpRequest',
                ...(options && options.headers ? options.headers : {}),
            },
            credentials: 'same-origin',
        });
        if (!response.ok || response.headers.get(partialHeaderName) !== partialHeaderValue) {
            throw new Error('Partial request failed');
        }
        const html = await response.text();
        return {
            html,
            currentUrl: response.headers.get(currentUrlHeaderName) || '',
            themeMode: response.headers.get(themeModeHeaderName) || '',
            themeCssUrl: response.headers.get(themeCssUrlHeaderName) || '',
            themeKey: response.headers.get(themeKeyHeaderName) || '',
            reloadUrl: response.headers.get(reloadUrlHeaderName) || '',
        };
    }

    function getFormActionUrl(form) {
        const action = form.getAttribute('action') || window.location.href;
        return new URL(action, window.location.origin);
    }

    function applyThemePayload(payload) {
        if (!payload) {
            return false;
        }
        if (payload.themeMode === 'reload') {
            window.location.assign(payload.reloadUrl || payload.currentUrl || window.location.href);
            return true;
        }
        if (payload.themeMode !== 'css') {
            return false;
        }
        const nextCssUrl = payload.themeCssUrl || '';
        const themeLink = document.getElementById('active-theme-css');
        if (nextCssUrl) {
            if (themeLink) {
                if (themeLink.getAttribute('href') !== nextCssUrl) {
                    themeLink.setAttribute('href', nextCssUrl);
                }
                if (payload.themeKey) {
                    themeLink.dataset.themeKey = payload.themeKey;
                }
            } else {
                const link = document.createElement('link');
                link.id = 'active-theme-css';
                link.rel = 'stylesheet';
                link.href = nextCssUrl;
                if (payload.themeKey) {
                    link.dataset.themeKey = payload.themeKey;
                }
                document.head.appendChild(link);
            }
            return false;
        }
        if (themeLink) {
            themeLink.remove();
            return false;
        }
        return false;
    }

    async function visit(url, { push = true } = {}) {
        if (!contentRoot) {
            window.location.assign(url);
            return;
        }
        shell.classList.add('is-loading');
        try {
            const payload = await fetchPartial(url, { method: 'GET' });
            contentRoot.innerHTML = payload.html;
            const nextUrl = payload.currentUrl || url;
            const didTriggerReload = applyThemePayload(payload);
            if (didTriggerReload) {
                return;
            }
            if (push) {
                window.history.pushState({ dashboardShell: true, url: nextUrl }, '', nextUrl);
            }
            syncTitleAndActiveStateFromContent();
        } catch (error) {
            window.location.assign(url);
            return;
        } finally {
            shell.classList.remove('is-loading');
        }
    }

    async function submitGetForm(form) {
        const formData = new window.FormData(form);
        const url = getFormActionUrl(form);
        const params = new URLSearchParams();
        formData.forEach((value, key) => {
            if (typeof value === 'string' && value !== '') {
                params.append(key, value);
            }
        });
        const finalUrl = url.pathname + (params.toString() ? `?${params.toString()}` : '');
        await visit(finalUrl);
    }

    document.addEventListener('click', (event) => {
        const anchor = event.target.closest('a[href]');
        if (!shouldHandleAnchor(anchor) || isModifiedClick(event)) {
            return;
        }
        event.preventDefault();
        const url = new URL(anchor.href, window.location.origin);
        visit(url.pathname + url.search + url.hash);
    });

    async function submitPostForm(form) {
        const action = getFormActionUrl(form);
        const formData = new window.FormData(form);
        const submitter = submitPostForm.lastSubmitter;
        if (submitter && submitter.name) {
            formData.append(submitter.name, submitter.value || '');
        }

        shell.classList.add('is-loading');
        try {
            const payload = await fetchPartial(action.pathname + action.search + action.hash, {
                method: 'POST',
                body: formData,
            });
            contentRoot.innerHTML = payload.html;
            const nextUrl = payload.currentUrl || window.location.pathname + window.location.search + window.location.hash;
            const didTriggerReload = applyThemePayload(payload);
            if (didTriggerReload) {
                return;
            }
            window.history.replaceState({ dashboardShell: true, url: nextUrl }, '', nextUrl);
            syncTitleAndActiveStateFromContent();
        } catch (error) {
            form.submit();
            return;
        } finally {
            shell.classList.remove('is-loading');
            submitPostForm.lastSubmitter = null;
        }
    }

    document.addEventListener('click', (event) => {
        const submitter = event.target.closest('button[type="submit"], input[type="submit"]');
        if (!submitter) {
            return;
        }
        submitPostForm.lastSubmitter = submitter;
    });

    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (!form || !shell.contains(form)) {
            return;
        }
        const method = (form.method || 'get').toLowerCase();
        if (method === 'get') {
            event.preventDefault();
            submitGetForm(form);
            return;
        }
        if (method === 'post' && form.matches('[data-dashboard-shell-form]')) {
            event.preventDefault();
            submitPostForm(form);
        }
    });

    window.addEventListener('popstate', () => {
        visit(window.location.pathname + window.location.search + window.location.hash, { push: false });
    });

    if (sidebar) {
        sidebar.querySelectorAll('[data-dashboard-group]').forEach((group) => {
            group.addEventListener('toggle', persistGroupState);
        });
    }

    function initCeleryTaskScheduleFields(scope) {
        const root = scope || document;
        const modeField = root.querySelector('#id_schedule_mode');
        if (!modeField || modeField.dataset.dashboardInitBound === 'true') {
            return;
        }

        function syncScheduleFields() {
            const isHourly = modeField.value === 'hourly';
            root.querySelectorAll('.js-schedule-field--hourly').forEach((node) => {
                node.style.display = isHourly ? '' : 'none';
            });
            root.querySelectorAll('.js-schedule-field--interval').forEach((node) => {
                node.style.display = isHourly ? 'none' : '';
            });
        }

        modeField.addEventListener('change', syncScheduleFields);
        modeField.dataset.dashboardInitBound = 'true';
        syncScheduleFields();
    }

    function initSteamCatalogPriceHistory(scope) {
        const root = scope || document;
        const detailPage = root.querySelector('[data-steam-catalog-detail-page]');
        if (!detailPage || detailPage.dataset.dashboardInitBound === 'true') {
            return;
        }

        detailPage.dataset.dashboardInitBound = 'true';
        detailPage.addEventListener('click', async (event) => {
            const link = event.target.closest('[data-price-range-link]');
            if (!link) {
                return;
            }
            if (isModifiedClick(event)) {
                return;
            }

            const currentSection = detailPage.querySelector('#price-history');
            if (!currentSection) {
                return;
            }

            event.preventDefault();
            if (detailPage.dataset.priceHistoryLoading === 'true') {
                return;
            }

            detailPage.dataset.priceHistoryLoading = 'true';
            currentSection.classList.add('is-loading');
            try {
                const response = await window.fetch(link.href, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    credentials: 'same-origin',
                });
                if (!response.ok) {
                    throw new Error('Failed to load price history section');
                }

                const html = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const nextSection = doc.querySelector('#price-history');
                if (!nextSection) {
                    throw new Error('Updated price history section not found');
                }

                currentSection.replaceWith(nextSection);
                window.history.replaceState({ dashboardShell: true, url: link.href }, '', link.href);
                const insertedSection = detailPage.querySelector('#price-history');
                if (insertedSection) {
                    insertedSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            } catch (error) {
                window.location.assign(link.href);
                return;
            } finally {
                detailPage.dataset.priceHistoryLoading = 'false';
                const section = detailPage.querySelector('#price-history');
                if (section) {
                    section.classList.remove('is-loading');
                }
            }
        });
    }

    function initSteamTradingAdd(scope) {
        const root = scope || document;
        const form = root.querySelector('#steam-trading-form');
        if (!form || form.dataset.dashboardInitBound === 'true') {
            return;
        }

        const itemField = form.querySelector('#id_item');
        const quantityField = form.querySelector('#id_quantity');
        const priceField = form.querySelector('#id_purchase_price_rub');
        const totalField = form.querySelector('#purchase_total_preview');
        if (!itemField || !quantityField || !priceField || !totalField) {
            return;
        }

        let itemPrices = {};
        try {
            itemPrices = JSON.parse(form.dataset.itemPrices || '{}');
        } catch (error) {
            itemPrices = {};
        }

        function parseNumber(value) {
            const normalized = String(value || '').replace(',', '.');
            const parsed = Number.parseFloat(normalized);
            return Number.isFinite(parsed) ? parsed : 0;
        }

        function syncPriceFromItem() {
            const currentValue = (priceField.value || '').trim();
            if (!currentValue || parseNumber(currentValue) === 0) {
                const nextPrice = itemPrices[itemField.value];
                if (nextPrice !== undefined) {
                    priceField.value = nextPrice;
                }
            }
        }

        function updateTotal() {
            const total = parseNumber(quantityField.value) * parseNumber(priceField.value);
            totalField.value = total.toFixed(2);
        }

        itemField.addEventListener('change', () => {
            const nextPrice = itemPrices[itemField.value];
            if (nextPrice !== undefined) {
                priceField.value = nextPrice;
            }
            updateTotal();
        });
        quantityField.addEventListener('input', updateTotal);
        priceField.addEventListener('input', updateTotal);

        form.dataset.dashboardInitBound = 'true';
        syncPriceFromItem();
        updateTotal();
    }

    function initSteamTradingSell(scope) {
        const root = scope || document;
        const form = root.querySelector('#steam-trading-sale-form');
        if (!form || form.dataset.dashboardInitBound === 'true') {
            return;
        }

        const quantityField = form.querySelector('#id_quantity');
        const priceField = form.querySelector('#id_sale_price_rub');
        const previewGrid = form.querySelector('#sale-preview-grid');
        if (!quantityField || !priceField || !previewGrid) {
            return;
        }

        const totalField = form.querySelector('#sale-total-preview');
        const costField = form.querySelector('#sale-cost-preview');
        const profitField = form.querySelector('#sale-profit-preview');
        const profitPercentField = form.querySelector('#sale-profit-percent-preview');
        const resultField = form.querySelector('#sale-result-preview');
        const purchasePrice = Number.parseFloat((previewGrid.dataset.purchasePrice || '0').replace(',', '.')) || 0;

        function parseNumber(value) {
            const normalized = String(value || '').replace(',', '.');
            const parsed = Number.parseFloat(normalized);
            return Number.isFinite(parsed) ? parsed : 0;
        }

        function formatSignedMoney(value) {
            const rounded = value.toFixed(2);
            return value > 0 ? `+${rounded}` : rounded;
        }

        function formatSignedPercent(value) {
            const rounded = value.toFixed(2);
            return value > 0 ? `+${rounded}%` : `${rounded}%`;
        }

        function updatePreview() {
            const quantity = parseNumber(quantityField.value);
            const salePrice = parseNumber(priceField.value);
            const totalSale = quantity * salePrice;
            const costBasis = quantity * purchasePrice;
            const profit = totalSale - costBasis;
            const profitPercent = costBasis === 0 ? 0 : (profit / costBasis) * 100;
            const resultLabel = profit > 0 ? 'Сделка в плюс' : (profit < 0 ? 'Сделка в минус' : 'Сделка в ноль');

            totalField.textContent = totalSale.toFixed(2);
            costField.textContent = costBasis.toFixed(2);
            profitField.textContent = formatSignedMoney(profit);
            profitPercentField.textContent = formatSignedPercent(profitPercent);
            resultField.textContent = resultLabel;
        }

        quantityField.addEventListener('input', updatePreview);
        priceField.addEventListener('input', updatePreview);
        form.dataset.dashboardInitBound = 'true';
        updatePreview();
    }

    function getCsrfToken() {
        const tokenInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (tokenInput && tokenInput.value) {
            return tokenInput.value;
        }
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function showDashboardToast(message, level = 'info') {
        if (!message) {
            return;
        }
        let stack = document.querySelector('[data-dashboard-toast-stack]');
        if (!stack) {
            stack = document.createElement('div');
            stack.setAttribute('data-dashboard-toast-stack', '1');
            stack.style.position = 'fixed';
            stack.style.right = '1rem';
            stack.style.bottom = '1rem';
            stack.style.zIndex = '9999';
            stack.style.display = 'grid';
            stack.style.gap = '.5rem';
            document.body.appendChild(stack);
        }
        const toast = document.createElement('div');
        toast.textContent = message;
        toast.style.padding = '.55rem .8rem';
        toast.style.borderRadius = '.5rem';
        toast.style.boxShadow = '0 6px 20px rgba(0,0,0,.2)';
        toast.style.background = level === 'error' ? '#7f1d1d' : '#0f766e';
        toast.style.color = '#fff';
        toast.style.fontSize = '.875rem';
        stack.appendChild(toast);
        window.setTimeout(() => {
            toast.remove();
            if (stack && stack.childElementCount === 0) {
                stack.remove();
            }
        }, 2800);
    }

    function initLiveModuleToggle(scope) {
        const root = scope || document;
        root.querySelectorAll('[data-module-live-toggle]').forEach((button) => {
            if (button.dataset.dashboardInitBound === 'true') {
                return;
            }
            button.dataset.dashboardInitBound = 'true';
            button.addEventListener('click', async () => {
                const moduleKey = button.dataset.moduleKey;
                const url = button.dataset.moduleToggleUrl;
                const confirmMessage = button.dataset.moduleToggleConfirm || '';
                if (!moduleKey || !url) {
                    return;
                }
                const checkbox = root.querySelector(`#id_module__${moduleKey}`);
                if (!checkbox) {
                    return;
                }
                if (confirmMessage && !checkbox.checked && !window.confirm(confirmMessage)) {
                    return;
                }
                button.disabled = true;
                try {
                    const payload = new URLSearchParams();
                    payload.append('enabled', checkbox.checked ? '1' : '0');
                    const response = await window.fetch(url, {
                        method: 'POST',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'X-CSRFToken': getCsrfToken(),
                            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                        },
                        body: payload.toString(),
                        credentials: 'same-origin',
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok || !data.ok) {
                        showDashboardToast(data.error || 'Live module update failed.', 'error');
                        return;
                    }
                    checkbox.checked = Boolean(data.enabled);
                    showDashboardToast(data.message || 'Module state updated.');
                } catch (_error) {
                    showDashboardToast('Live module update failed.', 'error');
                } finally {
                    button.disabled = false;
                }
            });
        });
    }

    function initAvatarUpload(scope) {
        const root = scope || document;
        root.querySelectorAll('[data-avatar-upload]').forEach((upload) => {
            if (upload.dataset.avatarUploadBound === 'true') {
                return;
            }
            upload.dataset.avatarUploadBound = 'true';

            const input = upload.querySelector('input[type="file"]');
            const fileName = upload.querySelector('[data-avatar-upload-file-name]');
            const previewWrap = upload.querySelector('[data-avatar-upload-preview-wrap]');
            const preview = upload.querySelector('[data-avatar-upload-preview]');
            const emptyLabel = upload.dataset.avatarUploadEmpty || 'No file selected';
            if (!input) {
                return;
            }

            input.addEventListener('change', () => {
                const file = input.files && input.files.length ? input.files[0] : null;
                if (fileName) {
                    fileName.textContent = file ? file.name : emptyLabel;
                }
                if (!preview || !previewWrap) {
                    return;
                }
                if (preview.dataset.avatarObjectUrl) {
                    window.URL.revokeObjectURL(preview.dataset.avatarObjectUrl);
                    delete preview.dataset.avatarObjectUrl;
                }
                if (!file || !file.type || !file.type.startsWith('image/')) {
                    preview.removeAttribute('src');
                    previewWrap.hidden = true;
                    return;
                }
                const objectUrl = window.URL.createObjectURL(file);
                preview.dataset.avatarObjectUrl = objectUrl;
                preview.src = objectUrl;
                previewWrap.hidden = false;
            });
        });
    }

    let socialMemberMenusBound = false;

    function closeSocialMemberMenus({ exceptMenu = null } = {}) {
        document.querySelectorAll('[data-social-member-menu]').forEach((menu) => {
            if (exceptMenu && menu === exceptMenu) {
                return;
            }
            menu.hidden = true;
            const wrap = menu.closest('[data-social-member-menu-wrap]');
            const toggle = wrap ? wrap.querySelector('[data-social-member-menu-toggle]') : null;
            if (toggle) {
                toggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    function initSocialMemberMenus(scope) {
        const root = scope || document;
        root.querySelectorAll('[data-social-member-menu-toggle]').forEach((toggle) => {
            const wrap = toggle.closest('[data-social-member-menu-wrap]');
            const menu = wrap ? wrap.querySelector('[data-social-member-menu]') : null;
            if (!menu) {
                return;
            }
            toggle.setAttribute('aria-expanded', String(!menu.hidden));
        });

        if (socialMemberMenusBound) {
            return;
        }
        socialMemberMenusBound = true;

        document.addEventListener('click', (event) => {
            const toggle = event.target.closest('[data-social-member-menu-toggle]');
            if (toggle) {
                const wrap = toggle.closest('[data-social-member-menu-wrap]');
                const menu = wrap ? wrap.querySelector('[data-social-member-menu]') : null;
                if (!menu) {
                    return;
                }
                const shouldOpen = menu.hidden;
                closeSocialMemberMenus({ exceptMenu: menu });
                menu.hidden = !shouldOpen;
                toggle.setAttribute('aria-expanded', String(shouldOpen));
                return;
            }

            if (!event.target.closest('[data-social-member-menu-wrap]')) {
                closeSocialMemberMenus();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                closeSocialMemberMenus();
            }
        });
    }

    function initDashboardPageScripts(scope) {
        initCeleryTaskScheduleFields(scope);
        initSteamCatalogPriceHistory(scope);
        initSteamTradingAdd(scope);
        initSteamTradingSell(scope);
        initLiveModuleToggle(scope);
        initAvatarUpload(scope);
        initSocialMemberMenus(scope);

        if (window.CaseUPStoreRatingInput && typeof window.CaseUPStoreRatingInput.init === 'function') {
            window.CaseUPStoreRatingInput.init(scope || document);
        }
    }

    restoreGroupState();
    initRightSocialSidebar();
    syncTitleAndActiveStateFromContent();
    initDashboardPageScripts(document);
    document.addEventListener('dashboard:content-replaced', (event) => {
        initDashboardPageScripts(event.detail && event.detail.frame ? event.detail.frame : document);
    });
})();
