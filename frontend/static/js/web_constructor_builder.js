(function () {
    const root = document.querySelector('[data-wc-builder]');
    if (!root) {
        return;
    }

    const stateEl = document.getElementById('wc-builder-state');
    const variableList = root.querySelector('[data-builder-variable-list]');
    const variableSearch = root.querySelector('[data-builder-variable-search]');
    const areaList = root.querySelector('[data-builder-area-list]');
    const form = root.querySelector('[data-builder-form]');
    const saveState = root.querySelector('[data-builder-save-state]');
    const saveButton = root.querySelector('[data-builder-save]');
    const resetButton = root.querySelector('[data-builder-reset]');
    const focusVariablesButton = root.querySelector('[data-builder-focus-variables]');
    const focusStylesButton = root.querySelector('[data-builder-focus-styles]');
    const previewFrame = root.querySelector('[data-builder-preview-frame]');
    const previewStatus = root.querySelector('[data-builder-preview-status]');
    const previewMessage = root.querySelector('[data-builder-preview-message]');

    let builderState = {};
    try {
        builderState = JSON.parse(stateEl.textContent || '{}');
    } catch (error) {
        builderState = {};
    }

    const draft = builderState.draft || {};
    let selectedAreaKey = (builderState.areas && builderState.areas[0] && builderState.areas[0].area_key) || null;
    let previewHtml = '';

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function setSaveState(label) {
        if (saveState) saveState.textContent = label;
    }

    function setPreviewStatus(label, message) {
        if (previewStatus) previewStatus.textContent = label;
        if (previewMessage && message) previewMessage.textContent = message;
    }

    function ensureAreaState(areaKey) {
        draft.areas = draft.areas || {};
        draft.areas[areaKey] = draft.areas[areaKey] || {};
        return draft.areas[areaKey];
    }

    function renderInspector() {
        if (!form || !selectedAreaKey) return;
        const areaState = ensureAreaState(selectedAreaKey);
        form.elements.label.value = areaState.label || '';
        form.elements.content.value = areaState.content || '';
        form.elements.width.value = areaState.width || '';
        form.elements.height.value = areaState.height || '';
        form.elements.background.value = areaState.background || '';
        form.elements.color.value = areaState.color || '';
        form.elements.style_preset.value = areaState.style_preset || '';
        form.elements.is_visible.checked = areaState.is_visible !== false;
    }

    function bindAreaClicks() {
        if (!areaList) return;
        areaList.querySelectorAll('[data-area-key]').forEach((button) => {
            button.addEventListener('click', () => {
                selectedAreaKey = button.getAttribute('data-area-key');
                areaList.querySelectorAll('[data-area-key]').forEach((item) => item.classList.remove('is-active'));
                button.classList.add('is-active');
                renderInspector();
                updatePreviewDocument();
            });
        });
        const first = areaList.querySelector('[data-area-key]');
        if (first) first.classList.add('is-active');
    }

    async function fetchVariables(query) {
        const url = new URL(root.dataset.variablesUrl, window.location.origin);
        if (query) url.searchParams.set('q', query);
        const response = await fetch(url.toString(), {headers: {'X-Requested-With': 'XMLHttpRequest'}});
        const payload = await response.json();
        variableList.innerHTML = '';
        (payload.items || []).forEach((item) => {
            const el = document.createElement('button');
            el.type = 'button';
            el.className = 'wc-builder-variable-item';
            el.innerHTML = `<strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.key)}</span><small>${escapeHtml(item.example_value || item.resolver_hint || '')}</small>`;
            el.addEventListener('click', () => {
                if (!form || !selectedAreaKey) return;
                const areaState = ensureAreaState(selectedAreaKey);
                const current = form.elements.content.value || '';
                const token = `{{ ${item.key} }}`;
                form.elements.content.value = current ? `${current} ${token}` : token;
                areaState.content = form.elements.content.value;
                setSaveState('Draft changed');
                updatePreviewDocument();
            });
            variableList.appendChild(el);
        });
    }

    function collectDraft() {
        const areaState = ensureAreaState(selectedAreaKey);
        areaState.label = form.elements.label.value;
        areaState.content = form.elements.content.value;
        areaState.width = form.elements.width.value;
        areaState.height = form.elements.height.value;
        areaState.background = form.elements.background.value;
        areaState.color = form.elements.color.value;
        areaState.style_preset = form.elements.style_preset.value;
        areaState.is_visible = form.elements.is_visible.checked;
        return {
            page_id: builderState.page && builderState.page.id,
            route_key: builderState.page && builderState.page.route_key,
            selected_area_key: selectedAreaKey,
            areas: draft.areas || {},
        };
    }

    function renderDraftOverlay() {
        const areaEntries = Object.entries((draft && draft.areas) || {});
        const items = areaEntries.length
            ? areaEntries.map(([areaKey, value]) => {
                const content = value.content ? `<p>${escapeHtml(String(value.content).slice(0, 160))}</p>` : '<p>Empty draft</p>';
                const meta = [value.width, value.height, value.background, value.color, value.style_preset].filter(Boolean).map((part) => `<span>${escapeHtml(part)}</span>`).join('');
                return `<article class="wc-builder-preview-draft-item${areaKey === selectedAreaKey ? ' is-active' : ''}"><strong>${escapeHtml(value.label || areaKey)}</strong><small>${escapeHtml(areaKey)}</small>${content}<div class="wc-builder-preview-draft-meta">${meta}</div></article>`;
            }).join('')
            : '<article class="wc-builder-preview-draft-item"><strong>Draft is empty</strong><p>Сохрани первый override в inspector, и он появится в overlay preview.</p></article>';

        return `
            <aside class="wc-builder-preview-draft-panel">
                <div class="wc-builder-preview-draft-head">
                    <strong>Sandbox draft overlay</strong>
                    <span>${escapeHtml(root.dataset.pageTitle || '')}</span>
                </div>
                <div class="wc-builder-preview-draft-list">${items}</div>
            </aside>
        `;
    }

    function buildPreviewDocument(rawHtml) {
        const baseHref = new URL(root.dataset.previewOpenUrl || '/', window.location.origin).toString();
        const injectHead = `
            <base href="${escapeHtml(baseHref)}">
            <style>
                .wc-builder-preview-draft-panel{position:fixed;right:16px;bottom:16px;z-index:2147483646;width:min(340px,calc(100vw - 32px));max-height:55vh;overflow:auto;padding:14px;border-radius:18px;border:1px solid rgba(255,255,255,.12);background:rgba(5,10,24,.88);color:#fff;font:14px/1.4 Inter,system-ui,sans-serif;box-shadow:0 18px 50px rgba(0,0,0,.35);backdrop-filter:blur(14px)}
                .wc-builder-preview-draft-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}
                .wc-builder-preview-draft-head span{color:rgba(255,255,255,.66);font-size:12px}
                .wc-builder-preview-draft-list{display:grid;gap:10px}
                .wc-builder-preview-draft-item{padding:10px;border-radius:14px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08)}
                .wc-builder-preview-draft-item.is-active{border-color:rgba(124,156,255,.7);box-shadow:inset 0 0 0 1px rgba(124,156,255,.35)}
                .wc-builder-preview-draft-item small,.wc-builder-preview-draft-item p{display:block;margin:4px 0 0;color:rgba(255,255,255,.74)}
                .wc-builder-preview-draft-meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
                .wc-builder-preview-draft-meta span{display:inline-flex;padding:2px 8px;border-radius:999px;background:rgba(124,156,255,.14);color:#d7e3ff;font-size:11px}
            </style>
        `;
        const injectBody = renderDraftOverlay();

        let html = rawHtml || '';
        if (!html.trim()) {
            html = '<!doctype html><html><head></head><body><main><h1>Preview is empty</h1></main></body></html>';
        }
        if (/<head[^>]*>/i.test(html)) {
            html = html.replace(/<head([^>]*)>/i, `<head$1>${injectHead}`);
        } else {
            html = `<!doctype html><html><head>${injectHead}</head><body>${html}</body></html>`;
        }
        if (/<\/body>/i.test(html)) {
            html = html.replace(/<\/body>/i, `${injectBody}</body>`);
        } else {
            html += injectBody;
        }
        return html;
    }

    function renderPreviewPlaceholder(title, message) {
        const html = `<!doctype html><html><head><base href="${escapeHtml(window.location.origin + '/')}" /><style>body{margin:0;font:16px/1.5 Inter,system-ui,sans-serif;background:#0b1020;color:#fff}main{min-height:100vh;display:grid;place-items:center;padding:32px}.card{max-width:720px;padding:28px;border-radius:22px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08)}h1{margin:0 0 12px;font-size:28px}p{margin:0;color:rgba(255,255,255,.74)}</style></head><body><main><section class="card"><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p></section></main></body></html>`;
        if (previewFrame) {
            previewFrame.srcdoc = html;
        }
    }

    function updatePreviewDocument() {
        if (!previewFrame) {
            return;
        }
        if (!previewHtml) {
            renderPreviewPlaceholder('Preview not loaded', 'Live HTML еще не загружен в lab shell.');
            return;
        }
        previewFrame.srcdoc = buildPreviewDocument(previewHtml);
    }

    async function loadPreview() {
        const previewUrl = root.dataset.previewUrl || '';
        if (!previewUrl) {
            setPreviewStatus('Preview unavailable', 'Dynamic route требует sample entity/id. На этом шаге lab shell не дергает route с параметрами наугад.');
            renderPreviewPlaceholder('Preview unavailable', 'Для dynamic route нужен sample entity/id. Добавим resolver sample objects на следующей фазе.');
            return;
        }
        setPreviewStatus('Loading live preview', 'Подтягиваем реальную HTML-страницу в lab shell без iframe navigation.');
        try {
            const response = await fetch(previewUrl, {
                method: 'GET',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CaseUP-Builder-Preview': '1',
                },
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            previewHtml = await response.text();
            updatePreviewDocument();
            setPreviewStatus('Live preview synced', 'HTML страницы загружен в iframe.srcdoc. X-Frame-Options route больше не мешает preview.');
        } catch (error) {
            previewHtml = '';
            setPreviewStatus('Preview failed', `Не удалось загрузить ${previewUrl}: ${error.message}`);
            renderPreviewPlaceholder('Preview failed', `Не удалось загрузить ${previewUrl}: ${error.message}`);
        }
    }

    async function saveDraft() {
        const response = await fetch(root.dataset.saveUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(collectDraft()),
        });
        if (!response.ok) {
            setSaveState('Save failed');
            return;
        }
        setSaveState('Draft saved');
        updatePreviewDocument();
    }

    async function resetDraft() {
        const response = await fetch(root.dataset.resetUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
        });
        if (!response.ok) {
            setSaveState('Reset failed');
            return;
        }
        draft.areas = {};
        renderInspector();
        setSaveState('Draft reset');
        updatePreviewDocument();
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : '';
    }

    if (form) {
        form.addEventListener('input', () => {
            collectDraft();
            setSaveState('Draft changed');
            updatePreviewDocument();
        });
    }
    if (variableSearch) {
        variableSearch.addEventListener('input', () => fetchVariables(variableSearch.value));
    }
    if (saveButton) saveButton.addEventListener('click', saveDraft);
    if (resetButton) resetButton.addEventListener('click', resetDraft);
    if (focusVariablesButton) focusVariablesButton.addEventListener('click', () => variableSearch && variableSearch.focus());
    if (focusStylesButton) focusStylesButton.addEventListener('click', () => form && form.elements.style_preset.focus());

    bindAreaClicks();
    renderInspector();
    fetchVariables('');
    loadPreview();
})();
