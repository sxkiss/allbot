/**
 * @input: schema + values API 数据、Bootstrap 表单组件
 * @output: 可视化配置表单渲染、列表/多号卡片编辑、表单采集与原文模式切换
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

    function defaultObjectItem(field) {
        const item = {};
        const itemFields = field.item_fields || [];
        itemFields.forEach((sub) => {
            if (Object.prototype.hasOwnProperty.call(field.default || {}, sub.key)) {
                item[sub.key] = field.default[sub.key];
                return;
            }
            if (sub.type === 'boolean') item[sub.key] = false;
            else if (sub.type === 'number') item[sub.key] = 0;
            else item[sub.key] = '';
        });
        if (field.type === 'object_map') {
            const keyField = field.key_field || 'name';
            if (!item[keyField]) item[keyField] = '';
        }
        return item;
    }

    function normalizeObjectList(value) {
        if (Array.isArray(value)) {
            return value.filter((item) => item && typeof item === 'object' && !Array.isArray(item));
        }
        return [];
    }

    function normalizeObjectMap(value, keyField) {
        const result = [];
        if (!value || typeof value !== 'object' || Array.isArray(value)) {
            return result;
        }
        Object.keys(value).forEach((name) => {
            const payload = value[name];
            if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return;
            const item = Object.assign({}, payload);
            if (!item[keyField]) item[keyField] = name;
            result.push(item);
        });
        return result;
    }

    function renderObjectSubField(sectionKey, fieldKey, itemIndex, subField, value) {
        const id = `${fieldId(sectionKey, fieldKey)}-${itemIndex}-${String(subField.key).replace(/\./g, '-')}`;
        const desc = subField.description ? `<div class="form-text">${escapeHtml(subField.description)}</div>` : '';
        const advancedAttr = subField.advanced ? '1' : '0';
        const common = `
            data-section="${escapeHtml(sectionKey)}"
            data-key="${escapeHtml(fieldKey)}"
            data-item-index="${itemIndex}"
            data-sub-key="${escapeHtml(subField.key)}"
            data-type="${escapeHtml(subField.type || 'text')}"
        `;

        if (subField.type === 'boolean') {
            const checked = value ? 'checked' : '';
            return `
                <div class="mb-2 config-object-subfield" data-advanced="${advancedAttr}">
                    <div class="form-check form-switch">
                        <input class="form-check-input config-object-input" type="checkbox" role="switch"
                               id="${escapeHtml(id)}" ${common} ${checked}>
                        <label class="form-check-label" for="${escapeHtml(id)}">${escapeHtml(subField.label)}</label>
                    </div>
                    ${desc}
                </div>
            `;
        }

        if (subField.type === 'textarea') {
            return `
                <div class="mb-2 config-object-subfield" data-advanced="${advancedAttr}">
                    <label class="form-label" for="${escapeHtml(id)}">${escapeHtml(subField.label)}</label>
                    <textarea class="form-control form-control-sm config-object-input" id="${escapeHtml(id)}" rows="2"
                              ${common} placeholder="${escapeHtml(subField.placeholder || '')}">${escapeHtml(value == null ? '' : value)}</textarea>
                    ${desc}
                </div>
            `;
        }

        const inputType = subField.type === 'number' ? 'number' : (subField.type === 'password' ? 'password' : 'text');
        return `
            <div class="mb-2 config-object-subfield" data-advanced="${advancedAttr}">
                <label class="form-label" for="${escapeHtml(id)}">${escapeHtml(subField.label)}</label>
                <input class="form-control form-control-sm config-object-input" id="${escapeHtml(id)}"
                       type="${inputType}" ${common}
                       value="${escapeHtml(value == null ? '' : value)}"
                       placeholder="${escapeHtml(subField.placeholder || '')}"
                       ${subField.secret ? 'autocomplete="new-password"' : ''}>
                ${desc}
            </div>
        `;
    }

    function renderObjectCard(sectionKey, field, item, index) {
        const itemFields = field.item_fields || [];
        const titleKey = field.key_field || (itemFields[0] && itemFields[0].key) || '';
        const titleValue = titleKey && item ? (item[titleKey] || '') : '';
        const title = titleValue || `${field.item_label || '项'} ${index + 1}`;
        const body = itemFields.map((subField) => {
            const value = item && Object.prototype.hasOwnProperty.call(item, subField.key)
                ? item[subField.key]
                : (subField.type === 'boolean' ? false : (subField.type === 'number' ? 0 : ''));
            return renderObjectSubField(sectionKey, field.key, index, subField, value);
        }).join('');

        return `
            <div class="config-object-card border rounded p-3 mb-2" data-item-index="${index}">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <div class="fw-semibold">
                        <i class="bi bi-person-badge me-1"></i>${escapeHtml(title)}
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-danger btn-remove-object-item" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
                <div class="row g-2">
                    <div class="col-12">
                        ${body}
                    </div>
                </div>
            </div>
        `;
    }

    function renderObjectCollectionField(sectionKey, field, value) {
        const id = fieldId(sectionKey, field.key);
        const keyField = field.key_field || 'name';
        let items = [];
        if (field.type === 'object_list') {
            items = normalizeObjectList(value);
        } else {
            items = normalizeObjectMap(value, keyField);
        }
        if (!items.length && field.default && typeof field.default === 'object' && !Array.isArray(field.default)) {
            // keep empty by default so user can add explicitly
        }
        const cards = items.map((item, index) => renderObjectCard(sectionKey, field, item, index)).join('');
        const addLabel = field.add_label || `添加${field.item_label || '项'}`;
        const emptyHint = items.length ? '' : `<div class="text-muted small mb-2">还没有${escapeHtml(field.item_label || '项')}，点击下方按钮添加。</div>`;
        return `
            <div class="mb-3 config-field" data-advanced="${field.advanced ? '1' : '0'}">
                <label class="form-label">${escapeHtml(field.label)}</label>
                ${field.description ? `<div class="form-text mb-2">${escapeHtml(field.description)}</div>` : ''}
                <div class="config-object-box" id="${escapeHtml(id)}"
                     data-section="${escapeHtml(sectionKey)}"
                     data-key="${escapeHtml(field.key)}"
                     data-type="${escapeHtml(field.type)}"
                     data-key-field="${escapeHtml(keyField)}"
                     data-item-fields="${escapeHtml(JSON.stringify(field.item_fields || []))}"
                     data-item-default="${escapeHtml(JSON.stringify(field.default || {}))}">
                    ${emptyHint}
                    ${cards}
                </div>
                <button type="button" class="btn btn-sm btn-outline-primary btn-add-object-item"
                        data-target="${escapeHtml(id)}">
                    <i class="bi bi-plus-lg me-1"></i>${escapeHtml(addLabel)}
                </button>
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

        if (field.type === 'object_list' || field.type === 'object_map') {
            return renderObjectCollectionField(sectionKey, field, value);
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
        bindObjectCollectionActions(container);
        bindSectionHeaderState(container);
    }

    function applyAdvancedVisibility(container, showAdvanced) {
        container.querySelectorAll('.config-field').forEach((el) => {
            if (el.getAttribute('data-advanced') === '1') {
                el.style.display = showAdvanced ? '' : 'none';
            }
        });
        container.querySelectorAll('.config-object-subfield').forEach((el) => {
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

    function parseJsonAttr(el, name, fallback) {
        try {
            const raw = el.getAttribute(name);
            if (!raw) return fallback;
            return JSON.parse(raw);
        } catch (err) {
            return fallback;
        }
    }

    function reindexObjectBox(box) {
        const cards = Array.from(box.querySelectorAll('.config-object-card'));
        cards.forEach((card, index) => {
            card.setAttribute('data-item-index', String(index));
            card.querySelectorAll('.config-object-input').forEach((input) => {
                input.setAttribute('data-item-index', String(index));
            });
            const titleEl = card.querySelector('.fw-semibold');
            if (titleEl) {
                const keyField = box.getAttribute('data-key-field') || 'name';
                const keyInput = card.querySelector(`.config-object-input[data-sub-key="${keyField}"]`);
                const label = (keyInput && keyInput.value) || `${box.getAttribute('data-key') || '项'} ${index + 1}`;
                titleEl.innerHTML = `<i class="bi bi-person-badge me-1"></i>${escapeHtml(label)}`;
            }
        });
        const empty = box.querySelector('.text-muted.small.mb-2');
        if (!cards.length && !empty) {
            const hint = document.createElement('div');
            hint.className = 'text-muted small mb-2';
            hint.textContent = '还没有项，点击下方按钮添加。';
            box.prepend(hint);
        } else if (cards.length && empty) {
            empty.remove();
        }
    }

    function bindObjectCollectionActions(container) {
        container.querySelectorAll('.btn-add-object-item').forEach((btn) => {
            btn.addEventListener('click', () => {
                const targetId = btn.getAttribute('data-target');
                const box = document.getElementById(targetId);
                if (!box) return;
                const section = box.getAttribute('data-section');
                const key = box.getAttribute('data-key');
                const type = box.getAttribute('data-type') || 'object_list';
                const keyField = box.getAttribute('data-key-field') || 'name';
                const itemFields = parseJsonAttr(box, 'data-item-fields', []);
                const itemDefault = parseJsonAttr(box, 'data-item-default', {});
                const field = {
                    key,
                    type,
                    key_field: keyField,
                    item_fields: itemFields,
                    default: itemDefault,
                    item_label: '项',
                };
                const item = defaultObjectItem(field);
                if (type === 'object_map') {
                    const existing = collectObjectMap(box);
                    let base = item[keyField] || 'item';
                    let candidate = base;
                    let n = 2;
                    while (Object.prototype.hasOwnProperty.call(existing, candidate)) {
                        candidate = `${base}${n}`;
                        n += 1;
                    }
                    item[keyField] = candidate;
                } else if (!item.name && itemFields.some((f) => f.key === 'name')) {
                    item.name = `bot${box.querySelectorAll('.config-object-card').length + 1}`;
                }
                const index = box.querySelectorAll('.config-object-card').length;
                const wrapper = document.createElement('div');
                wrapper.innerHTML = renderObjectCard(section, field, item, index).trim();
                const card = wrapper.firstElementChild;
                if (card) box.appendChild(card);
                reindexObjectBox(box);
                applyAdvancedVisibility(container, !!(container.closest('body') && document.getElementById('adapter-show-advanced') && document.getElementById('adapter-show-advanced').checked)
                    || !!(document.getElementById('settings-show-advanced') && document.getElementById('settings-show-advanced').checked)
                    || !!(document.getElementById('plugin-show-advanced') && document.getElementById('plugin-show-advanced').checked));
            });
        });

        container.addEventListener('click', (event) => {
            const btn = event.target.closest('.btn-remove-object-item');
            if (!btn) return;
            const card = btn.closest('.config-object-card');
            const box = btn.closest('.config-object-box');
            if (card) card.remove();
            if (box) reindexObjectBox(box);
        });

        container.addEventListener('input', (event) => {
            const input = event.target.closest('.config-object-input');
            if (!input) return;
            const box = input.closest('.config-object-box');
            if (!box) return;
            const keyField = box.getAttribute('data-key-field') || 'name';
            if (input.getAttribute('data-sub-key') === keyField || input.getAttribute('data-sub-key') === 'name') {
                reindexObjectBox(box);
            }
        });
    }

    function readObjectInputValue(input) {
        const type = input.getAttribute('data-type') || 'text';
        if (type === 'boolean') return !!input.checked;
        if (type === 'number') {
            const raw = input.value;
            if (raw === '' || raw == null) return 0;
            if (String(raw).includes('.')) return Number(raw);
            return parseInt(raw, 10);
        }
        return input.value;
    }

    function collectObjectList(box) {
        const cards = Array.from(box.querySelectorAll('.config-object-card'));
        return cards.map((card) => {
            const item = {};
            card.querySelectorAll('.config-object-input').forEach((input) => {
                const subKey = input.getAttribute('data-sub-key');
                if (!subKey) return;
                item[subKey] = readObjectInputValue(input);
            });
            return item;
        }).filter((item) => {
            // keep cards that have any non-empty meaningful value
            return Object.keys(item).some((key) => {
                const val = item[key];
                if (typeof val === 'boolean') return true;
                if (typeof val === 'number') return true;
                return String(val || '').trim() !== '';
            });
        });
    }

    function collectObjectMap(box) {
        const keyField = box.getAttribute('data-key-field') || 'name';
        const items = collectObjectList(box);
        const result = {};
        items.forEach((item, index) => {
            let key = String(item[keyField] || '').trim();
            if (!key) key = `item${index + 1}`;
            let finalKey = key;
            let n = 2;
            while (Object.prototype.hasOwnProperty.call(result, finalKey)) {
                finalKey = `${key}${n}`;
                n += 1;
            }
            item[keyField] = finalKey;
            result[finalKey] = item;
        });
        return result;
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

        container.querySelectorAll('.config-object-box').forEach((box) => {
            const section = box.getAttribute('data-section');
            const key = box.getAttribute('data-key');
            const type = box.getAttribute('data-type') || 'object_list';
            if (!section || !key) return;
            if (!result[section]) result[section] = {};
            if (type === 'object_map') {
                result[section][key] = collectObjectMap(box);
            } else {
                result[section][key] = collectObjectList(box);
            }
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
