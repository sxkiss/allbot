/**
 * @input: schema + values API 数据、Bootstrap 表单组件
 * @output: 可视化配置表单渲染、列表编辑、表单采集与原文模式切换
 * @position: 管理后台通用配置编辑器，供系统设置/插件/适配器复用
 * @auto-doc: Update header and folder INDEX.md when this file changes
 */

(function (global) {
    'use strict';

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function fieldId(sectionKey, fieldKey) {
        return `cfg-${sectionKey}-${String(fieldKey).replace(/\./g, '-')}`;
    }

    function ensureArray(value) {
        if (Array.isArray(value)) {
            return value.map((item) => String(item));
        }
        if (value == null || value === '') {
            return [];
        }
        return [String(value)];
    }

    function getValue(values, sectionKey, fieldKey) {
        const section = (values && values[sectionKey]) || {};
        if (Object.prototype.hasOwnProperty.call(section, fieldKey)) {
            return section[fieldKey];
        }
        return undefined;
    }

    function renderListField(sectionKey, field, value) {
        const items = ensureArray(value);
        const id = fieldId(sectionKey, field.key);
        const rows = items.map((item, index) => `
            <div class="input-group array-input-group mb-2" data-list-row="${escapeHtml(id)}">
                <input type="text" class="form-control config-list-item"
                       data-section="${escapeHtml(sectionKey)}"
                       data-key="${escapeHtml(field.key)}"
                       value="${escapeHtml(item)}"
                       placeholder="${escapeHtml(field.item_label || '项')}">
                <button class="btn btn-outline-danger btn-remove-list-item" type="button" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `).join('');

        return `
            <div class="mb-3 config-field" data-advanced="${field.advanced ? '1' : '0'}">
                <label class="form-label">${escapeHtml(field.label)}</label>
                <div class="config-list-box" id="${escapeHtml(id)}"
                     data-section="${escapeHtml(sectionKey)}"
                     data-key="${escapeHtml(field.key)}"
                     data-type="list">
                    ${rows || ''}
                </div>
                <button type="button" class="btn btn-sm btn-outline-primary btn-add-list-item"
                        data-target="${escapeHtml(id)}"
                        data-placeholder="${escapeHtml(field.item_label || '项')}">
                    <i class="bi bi-plus-lg me-1"></i>添加${escapeHtml(field.item_label || '项')}
                </button>
                ${field.description ? `<div class="form-text">${escapeHtml(field.description)}</div>` : ''}
            </div>
        `;
    }

    function renderField(sectionKey, field, value) {
        const id = fieldId(sectionKey, field.key);
        const advancedAttr = field.advanced ? '1' : '0';
        const disabled = field.readonly ? 'disabled' : '';
        const desc = field.description ? `<div class="form-text">${escapeHtml(field.description)}</div>` : '';
        const unit = field.unit ? ` <span class="text-muted small">(${escapeHtml(field.unit)})</span>` : '';

        if (field.type === 'boolean') {
            const checked = value ? 'checked' : '';
            return `
                <div class="mb-3 config-field" data-advanced="${advancedAttr}">
                    <div class="form-check form-switch">
                        <input class="form-check-input config-input" type="checkbox" role="switch"
                               id="${escapeHtml(id)}"
                               data-section="${escapeHtml(sectionKey)}"
                               data-key="${escapeHtml(field.key)}"
                               data-type="boolean"
                               ${checked} ${disabled}>
                        <label class="form-check-label" for="${escapeHtml(id)}">${escapeHtml(field.label)}${unit}</label>
                    </div>
                    ${desc}
                </div>
            `;
        }

        if (field.type === 'list') {
            return renderListField(sectionKey, field, value);
        }

        if (field.type === 'select') {
            const options = (field.options || []).map((opt) => {
                const selected = String(value) === String(opt.value) ? 'selected' : '';
                return `<option value="${escapeHtml(opt.value)}" ${selected}>${escapeHtml(opt.label)}</option>`;
            }).join('');
            return `
                <div class="mb-3 config-field" data-advanced="${advancedAttr}">
                    <label class="form-label" for="${escapeHtml(id)}">${escapeHtml(field.label)}${unit}</label>
                    <select class="form-select config-input" id="${escapeHtml(id)}"
                            data-section="${escapeHtml(sectionKey)}"
                            data-key="${escapeHtml(field.key)}"
                            data-type="select" ${disabled}>
                        ${options}
                    </select>
                    ${desc}
                </div>
            `;
        }

        if (field.type === 'textarea') {
            return `
                <div class="mb-3 config-field" data-advanced="${advancedAttr}">
                    <label class="form-label" for="${escapeHtml(id)}">${escapeHtml(field.label)}${unit}</label>
                    <textarea class="form-control config-input" id="${escapeHtml(id)}" rows="3"
                              data-section="${escapeHtml(sectionKey)}"
                              data-key="${escapeHtml(field.key)}"
                              data-type="textarea"
                              placeholder="${escapeHtml(field.placeholder || '')}" ${disabled}>${escapeHtml(value == null ? '' : value)}</textarea>
                    ${desc}
                </div>
            `;
        }

        const inputType = field.type === 'number' ? 'number' : (field.type === 'password' ? 'password' : 'text');
        const minAttr = field.min != null ? `min="${escapeHtml(field.min)}"` : '';
        const maxAttr = field.max != null ? `max="${escapeHtml(field.max)}"` : '';
        return `
            <div class="mb-3 config-field" data-advanced="${advancedAttr}">
                <label class="form-label" for="${escapeHtml(id)}">${escapeHtml(field.label)}${unit}</label>
                <input class="form-control config-input" id="${escapeHtml(id)}"
                       type="${inputType}"
                       data-section="${escapeHtml(sectionKey)}"
                       data-key="${escapeHtml(field.key)}"
                       data-type="${escapeHtml(field.type || 'text')}"
                       value="${escapeHtml(value == null ? '' : value)}"
                       placeholder="${escapeHtml(field.placeholder || '')}"
                       ${minAttr} ${maxAttr} ${disabled}
                       ${field.secret ? 'autocomplete="new-password"' : ''}>
                ${desc}
            </div>
        `;
    }

    function renderSchema(container, schema, values, options) {
        const opts = options || {};
        const showAdvanced = !!opts.showAdvanced;
        const html = (schema || []).map((section) => {
            const fieldsHtml = (section.fields || []).map((field) => {
                if (!showAdvanced && field.advanced) {
                    // still render but hidden, so values preserved
                }
                const value = getValue(values, section.key, field.key);
                return renderField(section.key, field, value);
            }).join('');

            return `
                <div class="config-section mb-3" data-section="${escapeHtml(section.key)}">
                    <div class="section-header" data-bs-toggle="collapse" data-bs-target="#section-${escapeHtml(section.key)}" aria-expanded="true">
                        <div>
                            <i class="bi ${escapeHtml(section.icon || 'bi-gear')} me-2"></i>
                            <strong>${escapeHtml(section.title || section.key)}</strong>
                            <div class="small opacity-75">${escapeHtml(section.description || '')}</div>
                        </div>
                        <i class="bi bi-chevron-down"></i>
                    </div>
                    <div class="collapse show section-body" id="section-${escapeHtml(section.key)}">
                        <div class="row">
                            <div class="col-12">
                                ${fieldsHtml || '<div class="text-muted">暂无字段</div>'}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        container.innerHTML = html || '<div class="alert alert-warning mb-0">未识别到可编辑配置项，请使用高级原文模式。</div>';
        applyAdvancedVisibility(container, showAdvanced);
        bindListActions(container);
        bindSectionHeaderState(container);
    }

    function applyAdvancedVisibility(container, showAdvanced) {
        container.querySelectorAll('.config-field').forEach((el) => {
            if (el.getAttribute('data-advanced') === '1') {
                el.style.display = showAdvanced ? '' : 'none';
            }
        });
    }

    function bindSectionHeaderState(container) {
        container.querySelectorAll('.section-header').forEach((header) => {
            const targetSelector = header.getAttribute('data-bs-target');
            const target = targetSelector ? container.querySelector(targetSelector) : null;
            if (!target) return;
            target.addEventListener('hide.bs.collapse', () => header.classList.add('collapsed'));
            target.addEventListener('show.bs.collapse', () => header.classList.remove('collapsed'));
        });
    }

    function bindListActions(container) {
        container.querySelectorAll('.btn-add-list-item').forEach((btn) => {
            btn.addEventListener('click', () => {
                const targetId = btn.getAttribute('data-target');
                const box = document.getElementById(targetId);
                if (!box) return;
                const placeholder = btn.getAttribute('data-placeholder') || '项';
                const section = box.getAttribute('data-section');
                const key = box.getAttribute('data-key');
                const row = document.createElement('div');
                row.className = 'input-group array-input-group mb-2';
                row.innerHTML = `
                    <input type="text" class="form-control config-list-item"
                           data-section="${escapeHtml(section)}"
                           data-key="${escapeHtml(key)}"
                           value=""
                           placeholder="${escapeHtml(placeholder)}">
                    <button class="btn btn-outline-danger btn-remove-list-item" type="button" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                `;
                box.appendChild(row);
            });
        });

        container.addEventListener('click', (event) => {
            const btn = event.target.closest('.btn-remove-list-item');
            if (!btn) return;
            const row = btn.closest('.input-group');
            if (row) row.remove();
        });
    }

    function collectValues(container) {
        const result = {};

        container.querySelectorAll('.config-input').forEach((input) => {
            const section = input.getAttribute('data-section');
            const key = input.getAttribute('data-key');
            const type = input.getAttribute('data-type') || 'text';
            if (!section || !key) return;
            if (!result[section]) result[section] = {};

            if (type === 'boolean') {
                result[section][key] = !!input.checked;
            } else if (type === 'number') {
                const raw = input.value;
                if (raw === '' || raw == null) {
                    result[section][key] = 0;
                } else if (String(raw).includes('.')) {
                    result[section][key] = Number(raw);
                } else {
                    result[section][key] = parseInt(raw, 10);
                }
            } else {
                result[section][key] = input.value;
            }
        });

        container.querySelectorAll('.config-list-box').forEach((box) => {
            const section = box.getAttribute('data-section');
            const key = box.getAttribute('data-key');
            if (!section || !key) return;
            if (!result[section]) result[section] = {};
            const items = Array.from(box.querySelectorAll('.config-list-item'))
                .map((input) => String(input.value || '').trim())
                .filter((item) => item !== '');
            result[section][key] = items;
        });

        return result;
    }

    function createController(options) {
        const opts = options || {};
        const formContainer = typeof opts.formContainer === 'string'
            ? document.querySelector(opts.formContainer)
            : opts.formContainer;
        const rawEditor = typeof opts.rawEditor === 'string'
            ? document.querySelector(opts.rawEditor)
            : opts.rawEditor;
        const modeVisualBtn = typeof opts.modeVisualBtn === 'string'
            ? document.querySelector(opts.modeVisualBtn)
            : opts.modeVisualBtn;
        const modeRawBtn = typeof opts.modeRawBtn === 'string'
            ? document.querySelector(opts.modeRawBtn)
            : opts.modeRawBtn;
        const advancedToggle = typeof opts.advancedToggle === 'string'
            ? document.querySelector(opts.advancedToggle)
            : opts.advancedToggle;
        const visualPane = typeof opts.visualPane === 'string'
            ? document.querySelector(opts.visualPane)
            : opts.visualPane;
        const rawPane = typeof opts.rawPane === 'string'
            ? document.querySelector(opts.rawPane)
            : opts.rawPane;

        let schema = [];
        let values = {};
        let raw = '';
        let mode = 'visual';
        let showAdvanced = !!(advancedToggle && advancedToggle.checked);

        function setMode(nextMode) {
            mode = nextMode === 'raw' ? 'raw' : 'visual';
            if (visualPane) visualPane.classList.toggle('d-none', mode !== 'visual');
            if (rawPane) rawPane.classList.toggle('d-none', mode !== 'raw');
            if (modeVisualBtn) modeVisualBtn.classList.toggle('active', mode === 'visual');
            if (modeRawBtn) modeRawBtn.classList.toggle('active', mode === 'raw');
        }

        function load(data) {
            schema = data.schema || [];
            values = data.values || data.data || {};
            raw = data.raw || '';
            if (formContainer) {
                renderSchema(formContainer, schema, values, { showAdvanced });
            }
            if (rawEditor) {
                rawEditor.value = raw;
            }
            setMode(mode);
        }

        function refreshAdvanced() {
            showAdvanced = !!(advancedToggle && advancedToggle.checked);
            if (formContainer) {
                applyAdvancedVisibility(formContainer, showAdvanced);
            }
        }

        function collect() {
            if (mode === 'raw') {
                return {
                    mode: 'raw',
                    content: rawEditor ? rawEditor.value : raw,
                };
            }
            return {
                mode: 'visual',
                values: formContainer ? collectValues(formContainer) : values,
            };
        }

        if (modeVisualBtn) {
            modeVisualBtn.addEventListener('click', () => setMode('visual'));
        }
        if (modeRawBtn) {
            modeRawBtn.addEventListener('click', () => setMode('raw'));
        }
        if (advancedToggle) {
            advancedToggle.addEventListener('change', refreshAdvanced);
        }

        setMode('visual');

        return {
            load,
            collect,
            setMode,
            getMode: () => mode,
            refreshAdvanced,
        };
    }

    global.ConfigForm = {
        renderSchema,
        collectValues,
        createController,
        escapeHtml,
    };
})(window);
