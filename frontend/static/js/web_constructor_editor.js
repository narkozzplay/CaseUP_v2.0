(function () {
    function initEditor(scope) {
        const root = (scope || document).querySelector('[data-wc-editor]');
        if (!root || root.dataset.wcInitialized === 'true') {
            return;
        }
        root.dataset.wcInitialized = 'true';

        const manifestNode = document.getElementById('wc-editor-manifest');
        const assetsNode = document.getElementById('wc-editor-assets');
        const schemaNode = document.getElementById('wc-editor-schema');
        const form = root.querySelector('#wc-save-form');
        const schemaField = form ? form.querySelector('input[name="schema_json"]') : null;
        const pageStyleRoot = root.querySelector('[data-wc-page-style-root]');
        const blockListRoot = root.querySelector('[data-wc-block-list]');
        const propertyRoot = root.querySelector('[data-wc-property-root]');
        const previewRoot = root.querySelector('[data-wc-preview-root]');
        const previewState = root.querySelector('[data-wc-preview-state]');
        const externalLinksBadge = root.querySelector('[data-wc-external-links]');
        const localStateNode = root.querySelector('[data-wc-local-state]');
        const restoreButton = root.querySelector('[data-wc-restore-local]');

        if (!manifestNode || !assetsNode || !schemaNode || !schemaField || !pageStyleRoot || !blockListRoot || !propertyRoot || !previewRoot) {
            return;
        }

        const manifest = JSON.parse(manifestNode.textContent || '{}');
        const assets = JSON.parse(assetsNode.textContent || '[]');
        const initialSchema = JSON.parse(schemaNode.textContent || '{}');
        const previewUrl = root.dataset.previewUrl;
        const storageKey = 'caseup.web_constructor.' + (root.dataset.templateUuid || 'draft');
        const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
        const csrfToken = csrfInput ? csrfInput.value : '';

        let state = initialSchema;
        let selectedIndex = 0;
        let previewTimer = null;

        function clone(value) {
            return JSON.parse(JSON.stringify(value));
        }

        function uid() {
            return 'blk_' + Math.random().toString(16).slice(2, 10);
        }

        function setLocalStateLabel(text) {
            if (localStateNode) {
                localStateNode.textContent = text;
            }
        }

        function saveToLocalStorage() {
            window.localStorage.setItem(storageKey, JSON.stringify(state));
            setLocalStateLabel('Локальный draft сохранён в браузере');
        }

        function syncSchemaField() {
            schemaField.value = JSON.stringify(state);
            saveToLocalStorage();
        }

        function createInput(field, value, onChange) {
            let input;
            if (field.kind === 'textarea') {
                input = document.createElement('textarea');
                input.rows = 4;
                input.className = 'form-control';
                input.value = value || '';
            } else if (field.kind === 'choice') {
                input = document.createElement('select');
                input.className = 'form-control';
                (field.choices || []).forEach((choice) => {
                    const option = document.createElement('option');
                    option.value = choice;
                    option.textContent = choice;
                    option.selected = choice === value;
                    input.appendChild(option);
                });
            } else if (field.kind === 'asset') {
                input = document.createElement('select');
                input.className = 'form-control';
                const emptyOption = document.createElement('option');
                emptyOption.value = '';
                emptyOption.textContent = '—';
                input.appendChild(emptyOption);
                assets.forEach((asset) => {
                    const option = document.createElement('option');
                    option.value = String(asset.id);
                    option.textContent = `${asset.title} (#${asset.id})`;
                    option.selected = String(asset.id) === String(value || '');
                    input.appendChild(option);
                });
            } else {
                input = document.createElement('input');
                input.type = field.kind === 'url' ? 'url' : 'text';
                input.className = 'form-control';
                input.value = value || '';
            }
            input.addEventListener('input', () => onChange(input.value));
            input.addEventListener('change', () => onChange(input.value));
            return input;
        }

        function renderPageStyle() {
            pageStyleRoot.innerHTML = '';
            const style = state.page && state.page.style ? state.page.style : {};
            Object.entries(manifest.page_style || {}).forEach(([key, spec]) => {
                const field = document.createElement('div');
                field.className = 'form-field';
                const label = document.createElement('label');
                label.textContent = spec.label || key;
                field.appendChild(label);
                const input = createInput({ kind: spec.type || 'choice', choices: spec.choices || [] }, style[key], (value) => {
                    state.page.style[key] = spec.type === 'asset' ? (value ? Number(value) : null) : value;
                    syncSchemaField();
                    render();
                });
                field.appendChild(input);
                pageStyleRoot.appendChild(field);
            });
        }

        function moveBlock(index, direction) {
            const targetIndex = index + direction;
            if (targetIndex < 0 || targetIndex >= state.blocks.length) {
                return;
            }
            const moved = state.blocks.splice(index, 1)[0];
            state.blocks.splice(targetIndex, 0, moved);
            selectedIndex = targetIndex;
            syncSchemaField();
            render();
        }

        function renderBlockList() {
            blockListRoot.innerHTML = '';
            state.blocks.forEach((block, index) => {
                const item = document.createElement('div');
                item.className = 'wc-block-list-item' + (index === selectedIndex ? ' is-active' : '');

                const title = document.createElement('button');
                title.type = 'button';
                title.className = 'btn btn-secondary btn-block';
                title.textContent = `${block.label || block.type} · ${block.data && (block.data.title || block.data.name || block.id) ? (block.data.title || block.data.name || block.id) : block.id}`;
                title.addEventListener('click', () => {
                    selectedIndex = index;
                    render();
                });
                item.appendChild(title);

                const actions = document.createElement('div');
                actions.className = 'wc-inline-actions';
                [
                    { label: '↑', handler: () => moveBlock(index, -1) },
                    { label: '↓', handler: () => moveBlock(index, 1) },
                    {
                        label: block.visible ? 'Hide' : 'Show',
                        handler: () => {
                            state.blocks[index].visible = !state.blocks[index].visible;
                            syncSchemaField();
                            render();
                        },
                    },
                    {
                        label: 'Copy',
                        handler: () => {
                            const copyBlock = clone(state.blocks[index]);
                            copyBlock.id = uid();
                            state.blocks.splice(index + 1, 0, copyBlock);
                            selectedIndex = index + 1;
                            syncSchemaField();
                            render();
                        },
                    },
                    {
                        label: 'Del',
                        handler: () => {
                            state.blocks.splice(index, 1);
                            if (!state.blocks.length) {
                                addBlock('hero');
                                return;
                            }
                            selectedIndex = Math.max(0, Math.min(selectedIndex, state.blocks.length - 1));
                            syncSchemaField();
                            render();
                        },
                    },
                ].forEach((spec) => {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'btn btn-secondary btn-sm';
                    btn.textContent = spec.label;
                    btn.addEventListener('click', spec.handler);
                    actions.appendChild(btn);
                });
                item.appendChild(actions);
                blockListRoot.appendChild(item);
            });
        }

        function renderRepeater(field, listValue, onChange) {
            const wrap = document.createElement('div');
            wrap.className = 'wc-repeater';
            const items = Array.isArray(listValue) ? listValue : [];
            items.forEach((row, rowIndex) => {
                const card = document.createElement('div');
                card.className = 'wc-repeater-item';
                (field.fields || []).forEach((nested) => {
                    const fieldWrap = document.createElement('div');
                    fieldWrap.className = 'form-field';
                    const label = document.createElement('label');
                    label.textContent = nested.name;
                    fieldWrap.appendChild(label);
                    const input = createInput(nested, row[nested.name], (value) => {
                        row[nested.name] = nested.kind === 'asset' ? (value ? Number(value) : null) : value;
                        onChange(items);
                    });
                    fieldWrap.appendChild(input);
                    card.appendChild(fieldWrap);
                });
                const rowActions = document.createElement('div');
                rowActions.className = 'wc-inline-actions';
                const removeBtn = document.createElement('button');
                removeBtn.type = 'button';
                removeBtn.className = 'btn btn-secondary btn-sm';
                removeBtn.textContent = 'Remove';
                removeBtn.addEventListener('click', () => {
                    items.splice(rowIndex, 1);
                    onChange(items);
                });
                rowActions.appendChild(removeBtn);
                card.appendChild(rowActions);
                wrap.appendChild(card);
            });
            const addBtn = document.createElement('button');
            addBtn.type = 'button';
            addBtn.className = 'btn btn-secondary btn-sm';
            addBtn.textContent = 'Add item';
            addBtn.addEventListener('click', () => {
                const blank = {};
                (field.fields || []).forEach((nested) => {
                    blank[nested.name] = nested.kind === 'asset' ? null : '';
                });
                items.push(blank);
                onChange(items);
            });
            wrap.appendChild(addBtn);
            return wrap;
        }

        function renderProperties() {
            propertyRoot.innerHTML = '';
            const block = state.blocks[selectedIndex];
            if (!block) {
                propertyRoot.innerHTML = '<p class="text-muted">Выберите или добавьте блок.</p>';
                return;
            }
            const spec = manifest.blocks[block.type];
            const head = document.createElement('div');
            head.className = 'section-head-inline';
            head.innerHTML = `<h3 class="section-title">${spec.label}</h3><span class="status-pill">${block.visible ? 'Visible' : 'Hidden'}</span>`;
            propertyRoot.appendChild(head);

            const groups = [
                { label: 'Data', fields: spec.data_fields, target: block.data },
                { label: 'Style', fields: spec.style_fields, target: block.style },
            ];
            groups.forEach((group) => {
                const title = document.createElement('h4');
                title.className = 'section-title';
                title.textContent = group.label;
                propertyRoot.appendChild(title);
                (group.fields || []).forEach((field) => {
                    const fieldWrap = document.createElement('div');
                    fieldWrap.className = 'form-field';
                    const label = document.createElement('label');
                    label.textContent = field.name;
                    fieldWrap.appendChild(label);
                    if (field.kind === 'repeater') {
                        fieldWrap.appendChild(renderRepeater(field, group.target[field.name], (value) => {
                            group.target[field.name] = value;
                            syncSchemaField();
                            render();
                        }));
                    } else {
                        const input = createInput(field, group.target[field.name], (value) => {
                            group.target[field.name] = field.kind === 'asset' ? (value ? Number(value) : null) : value;
                            syncSchemaField();
                            render();
                        });
                        fieldWrap.appendChild(input);
                    }
                    propertyRoot.appendChild(fieldWrap);
                });
            });
        }

        function updatePreview() {
            if (!previewUrl) {
                return;
            }
            if (previewState) {
                previewState.textContent = 'Preview updating…';
            }
            const formData = new window.FormData();
            if (csrfToken) {
                formData.append('csrfmiddlewaretoken', csrfToken);
            }
            formData.append('schema_json', JSON.stringify(state));
            window.fetch(previewUrl, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
                .then((response) => response.text().then((html) => ({ response, html })))
                .then(({ response, html }) => {
                    if (!response.ok) {
                        throw new Error(html || 'Preview failed');
                    }
                    previewRoot.innerHTML = html;
                    if (previewState) {
                        previewState.textContent = 'Preview synced';
                    }
                    if (externalLinksBadge) {
                        externalLinksBadge.textContent = 'External links: ' + (response.headers.get('X-Constructor-External-Links-Count') || '0');
                    }
                })
                .catch(() => {
                    if (previewState) {
                        previewState.textContent = 'Preview error';
                    }
                });
        }

        function schedulePreview() {
            window.clearTimeout(previewTimer);
            previewTimer = window.setTimeout(updatePreview, 350);
        }

        function addBlock(type) {
            const spec = manifest.blocks[type];
            if (!spec) {
                return;
            }
            state.blocks.push({
                id: uid(),
                type: type,
                label: spec.label,
                visible: true,
                data: clone(spec.default_data || {}),
                style: clone(spec.default_style || {}),
            });
            selectedIndex = state.blocks.length - 1;
            syncSchemaField();
            render();
        }

        function renderAll() {
            renderPageStyle();
            renderBlockList();
            renderProperties();
            schedulePreview();
        }

        root.querySelectorAll('[data-wc-add-block]').forEach((button) => {
            button.addEventListener('click', () => addBlock(button.dataset.wcAddBlock));
        });

        if (restoreButton) {
            restoreButton.addEventListener('click', () => {
                const localState = window.localStorage.getItem(storageKey);
                if (!localState) {
                    return;
                }
                try {
                    state = JSON.parse(localState);
                    syncSchemaField();
                    renderAll();
                } catch (_error) {
                    // ignore broken local draft
                }
            });
        }

        const localSnapshot = window.localStorage.getItem(storageKey);
        if (localSnapshot && localSnapshot !== JSON.stringify(initialSchema)) {
            setLocalStateLabel('Найден локальный draft — можно восстановить');
        }

        syncSchemaField();
        renderAll();
    }

    document.addEventListener('DOMContentLoaded', function () {
        initEditor(document);
    });
    document.addEventListener('dashboard:content-replaced', function (event) {
        initEditor(event && event.detail ? event.detail.frame : document);
    });
})();
