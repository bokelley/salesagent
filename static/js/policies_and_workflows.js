/**
 * Policies & Workflows page — Sprint 7 Phase 2.
 *
 * Extracted from tenant_settings.js when this section promoted out of
 * Tenant Settings into a Configure → Workspace peer page. Same POST
 * endpoint (`/settings/business-rules`); the in-page form lifted
 * intact, only the surrounding chrome changed.
 *
 * Reads config from #settings-config data attributes.
 */

const config = (function () {
    const el = document.getElementById('settings-config');
    if (!el) {
        console.error('Policies config element not found');
        return {};
    }
    return {
        scriptName: el.dataset.scriptName || '',
        tenantId: el.dataset.tenantId || '',
    };
})();

// --------------------------------------------------------------------
// Business rules save (POSTs the whole form to /settings/business-rules)
// --------------------------------------------------------------------

function saveBusinessRules() {
    const form = document.getElementById('business-rules-form');

    // Add measurement provider inputs before creating FormData.
    // The form submits each provider as ``provider_name_<n>`` so the
    // server can iterate; the default is a separate radio.
    const container = document.getElementById('measurement-providers-container');
    const providerItems = container.querySelectorAll('.measurement-provider-item');

    const existingInputs = form.querySelectorAll('input[name^="provider_name_"]');
    existingInputs.forEach((input) => input.remove());

    providerItems.forEach((item, index) => {
        const textInput = item.querySelector('.provider-name-input');
        const providerName = textInput.value.trim();

        if (providerName) {
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.name = `provider_name_${index}`;
            hiddenInput.value = providerName;
            form.appendChild(hiddenInput);
        }
    });

    const checkedRadio = container.querySelector('input[name="default_measurement_provider"]:checked');
    if (checkedRadio) {
        const providerItem = checkedRadio.closest('.measurement-provider-item');
        const textInput = providerItem.querySelector('.provider-name-input');
        checkedRadio.value = textInput.value;
    }

    const formData = new FormData(form);

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/business-rules`, {
        method: 'POST',
        body: formData,
        redirect: 'follow',
    })
        .then((response) => {
            const contentType = response.headers.get('content-type') || '';
            const isHtml = contentType.includes('text/html');
            const isJson = contentType.includes('application/json');

            if (!response.ok) {
                if (isJson) {
                    return response.json().then((data) => {
                        throw new Error(data.message || data.error || `Server returned status ${response.status}`);
                    });
                }
                return response.text().then((body) => {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(body, 'text/html');
                    const flashContainer = doc.querySelector('.flash-messages');
                    if (flashContainer) {
                        const flashMessages = flashContainer.querySelectorAll('.alert');
                        if (flashMessages.length > 0) {
                            const messages = Array.from(flashMessages)
                                .map((el) => el.textContent.trim().replace('×', '').trim())
                                .join('\n\n');
                            throw new Error(messages);
                        }
                    }
                    throw new Error(`Server returned status ${response.status}`);
                });
            }

            if (isHtml) {
                return response.text().then((html) => {
                    const parser = new DOMParser();
                    const doc = parser.parseFromString(html, 'text/html');

                    const flashContainer = doc.querySelector('.flash-messages');
                    if (flashContainer) {
                        const flashMessages = flashContainer.querySelectorAll('.alert');
                        if (flashMessages.length > 0) {
                            const messages = Array.from(flashMessages)
                                .map((el) => el.textContent.trim().replace('×', '').trim())
                                .join('\n\n');

                            const isSuccess = flashMessages[0].classList.contains('alert-success');
                            if (isSuccess) {
                                window.location.reload();
                            } else {
                                alert('⚠️ ' + messages);
                            }
                            return;
                        }
                    }
                    window.location.reload();
                });
            }

            return response.json().then((data) => {
                if (data.success) {
                    window.location.reload();
                } else {
                    alert('Error: ' + (data.message || data.error || 'Unknown error'));
                }
            });
        })
        .catch((error) => {
            alert('Error: ' + error.message);
        });
}

// --------------------------------------------------------------------
// Approval mode + advertising policy UI toggles
// --------------------------------------------------------------------

function updateApprovalModeUI() {
    const approvalModeEl = document.getElementById('approval_mode');
    if (!approvalModeEl) return;
    const approvalMode = approvalModeEl.value;

    const ids = ['desc-auto-approve', 'desc-require-human', 'desc-ai-powered'];
    ids.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });

    const selectedDesc = document.getElementById(`desc-${approvalMode}`);
    if (selectedDesc) selectedDesc.style.display = 'block';

    const aiConfigSection = document.getElementById('ai-config-section');
    if (aiConfigSection) {
        aiConfigSection.style.display = approvalMode === 'ai-powered' ? 'block' : 'none';
    }
}

function updateAdvertisingPolicyUI() {
    const policyCheckEnabled = document.getElementById('policy_check_enabled');
    const policyConfigSection = document.getElementById('advertising-policy-config');

    if (policyCheckEnabled && policyConfigSection) {
        policyConfigSection.style.display = policyCheckEnabled.checked ? 'block' : 'none';
    }
}

// --------------------------------------------------------------------
// Currency limits (modal + add/remove)
// --------------------------------------------------------------------

function showAddCurrencyModal() {
    document.getElementById('new-currency-code').value = '';
    document.getElementById('new-currency-min').value = '';
    document.getElementById('new-currency-max').value = '';

    const modal = new bootstrap.Modal(document.getElementById('addCurrencyModal'));
    modal.show();
}

function addCurrencyLimit() {
    const currencyCode = document.getElementById('new-currency-code').value.trim().toUpperCase();
    const minBudget = document.getElementById('new-currency-min').value;
    const maxSpend = document.getElementById('new-currency-max').value;

    // Strict whitelist — the value is interpolated into innerHTML attribute
    // contexts six places below, so we need a regex that admits only safe
    // chars. The length check alone would let ``"AB`` or ``';X`` slip
    // through and break out of an HTML attribute → self-XSS.
    if (!/^[A-Z]{3}$/.test(currencyCode)) {
        alert('Please enter a valid 3-letter currency code (e.g., EUR, GBP, CAD)');
        return;
    }

    const existingCurrency = document.querySelector(`.currency-limit-item[data-currency="${currencyCode}"]`);
    if (existingCurrency) {
        const deleteField = existingCurrency.querySelector(
            `input[name="currency_limits[${currencyCode}][_delete]"]`,
        );
        const isMarkedForDeletion = deleteField && deleteField.value === 'true';

        if (!isMarkedForDeletion) {
            alert(`Currency ${currencyCode} already exists. Please edit the existing entry or remove it first.`);
            return;
        }
        existingCurrency.remove();
    }

    const container = document.getElementById('currency-limits-container');
    const newItem = document.createElement('div');
    newItem.className = 'currency-limit-item';
    newItem.setAttribute('data-currency', currencyCode);
    newItem.style.cssText =
        'display: flex; align-items: start; gap: 1rem; margin-bottom: 1rem; padding: 1rem; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;';

    newItem.innerHTML = `
        <div style="flex: 1;">
            <div style="display: grid; grid-template-columns: 150px 1fr 1fr; gap: 1rem; align-items: center;">
                <div>
                    <label style="display: block; font-weight: 600; color: #1f2937; margin-bottom: 0.25rem;">Currency</label>
                    <input type="text" readonly value="${currencyCode}"
                           style="padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px; background: #f3f4f6; width: 100%; font-weight: 600;">
                </div>
                <div>
                    <label style="display: block; font-size: 0.875rem; color: #4b5563; margin-bottom: 0.25rem;">
                        Min Package Budget
                    </label>
                    <input type="number"
                           name="currency_limits[${currencyCode}][min_package_budget]"
                           value="${minBudget}"
                           min="0" step="0.01"
                           placeholder="No minimum"
                           style="padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px; width: 100%;">
                </div>
                <div>
                    <label style="display: block; font-size: 0.875rem; color: #4b5563; margin-bottom: 0.25rem;">
                        Max Daily Package Spend
                    </label>
                    <input type="number"
                           name="currency_limits[${currencyCode}][max_daily_package_spend]"
                           value="${maxSpend}"
                           min="0" step="0.01"
                           placeholder="No maximum"
                           style="padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px; width: 100%;">
                </div>
            </div>
            <small style="display: block; color: #6b7280; margin-top: 0.5rem;">
                Limits apply per package/line item to prevent budget splitting
            </small>
        </div>
        <button type="button" class="btn btn-sm btn-danger" onclick="removeCurrencyLimit('${currencyCode}')" title="Remove Currency">
            <i class="fas fa-times"></i>
        </button>
        <input type="hidden" name="currency_limits[${currencyCode}][_delete]" value="false">
    `;

    container.appendChild(newItem);

    const modal = bootstrap.Modal.getInstance(document.getElementById('addCurrencyModal'));
    if (modal) modal.hide();

    alert(`✅ Currency ${currencyCode} added. Don't forget to save your changes!`);
}

function removeCurrencyLimit(currencyCode) {
    if (!confirm(`Are you sure you want to remove ${currencyCode}? This will affect any products using this currency.`)) {
        return;
    }

    const item = document.querySelector(`.currency-limit-item[data-currency="${currencyCode}"]`);
    if (item) {
        const deleteField = item.querySelector(`input[name="currency_limits[${currencyCode}][_delete]"]`);
        if (deleteField) {
            deleteField.value = 'true';
        }
        item.style.display = 'none';
    }
}

// --------------------------------------------------------------------
// Measurement providers (add row via "+ Add Provider")
// --------------------------------------------------------------------

function addMeasurementProvider() {
    const container = document.getElementById('measurement-providers-container');
    if (!container) return;
    const emptyState = container.querySelector('div[style*="dashed"]');
    if (emptyState) emptyState.remove();

    const item = document.createElement('div');
    item.className = 'measurement-provider-item';
    item.style.cssText =
        'display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; padding: 0.5rem; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px;';
    item.innerHTML = `
        <input type="radio" name="default_measurement_provider" value=""
               onchange="markDefaultProvider(this)">
        <input type="text" value="" class="provider-name-input"
               style="flex: 1; padding: 0.5rem; border: 1px solid #d1d5db; border-radius: 4px;"
               placeholder="e.g., Google Ad Manager with IAS">
        <button type="button" class="btn btn-sm btn-secondary" onclick="removeProvider(this)" title="Remove">
            <i class="fas fa-times"></i>
        </button>
    `;
    container.appendChild(item);
    item.querySelector('.provider-name-input').focus();
}

function removeProvider(button) {
    const item = button.closest('.measurement-provider-item');
    if (item) item.remove();
}

function markDefaultProvider(radio) {
    // No-op visual marker; saveBusinessRules reads the checked state on submit.
    void radio;
}

// --------------------------------------------------------------------
// Naming templates (preview + presets)
// --------------------------------------------------------------------

function resolveTemplate(template, context) {
    if (!template) return '';

    return template.replace(/\{([^}]+)\}/g, (match, key) => {
        const options = key.split('|');

        for (const option of options) {
            const val = context[option.trim()];
            if (val !== undefined && val !== null && val !== '') {
                return val;
            }
        }
        return match;
    });
}

function updateNamingPreview() {
    const orderTemplate = document.getElementById('order_name_template')?.value || '';
    const lineItemTemplate = document.getElementById('line_item_name_template')?.value || '';

    const context = {
        campaign_name: '',
        promoted_offering: 'Nike Shoes Q1',
        brand_name: 'Nike',
        buyer_ref: 'PO-12345',
        start_date: '2025-10-07',
        end_date: '2025-10-14',
        date_range: 'Oct 7-14, 2025',
        month_year: 'Oct 2025',
        package_count: 3,
        auto_name: 'Nike Shoes Q1 Campaign',
    };

    const orderName = resolveTemplate(orderTemplate, context);
    const orderPreviewEl = document.getElementById('order-preview');
    if (orderPreviewEl) orderPreviewEl.textContent = orderName;

    const products = [
        { name: 'Display 300x250', index: 1 },
        { name: 'Video Pre-roll', index: 2 },
        { name: 'Native Article', index: 3 },
    ];

    const lineItemNames = products.map((p) => {
        const itemContext = {
            ...context,
            order_name: orderName,
            product_name: p.name,
            package_index: p.index,
        };
        const name = resolveTemplate(lineItemTemplate, itemContext);
        return `${p.index}. ${name}`;
    });

    const lineItemPreviewEl = document.getElementById('lineitem-preview');
    if (lineItemPreviewEl) {
        lineItemPreviewEl.innerHTML = lineItemNames.join('<br>');
    }
}

function useNamingPreset(presetName) {
    const presets = {
        simple: {
            order: '{campaign_name} - {start_date}',
            lineItem: '{product_name}',
        },
        campaign: {
            order: '{campaign_name} - {buyer_ref}',
            lineItem: '{campaign_name} - {product_name}',
        },
        detailed: {
            order: '{campaign_name|brand_name} - {buyer_ref} - {date_range}',
            lineItem: '{order_name} - {product_name}',
        },
    };

    const preset = presets[presetName];
    if (!preset) {
        console.error('Unknown preset:', presetName);
        return;
    }

    const orderField = document.getElementById('order_name_template');
    const lineItemField = document.getElementById('line_item_name_template');

    if (orderField) orderField.value = preset.order;
    if (lineItemField) lineItemField.value = preset.lineItem;

    updateNamingPreview();
}

// --------------------------------------------------------------------
// On-load wiring
// --------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('approval_mode')) {
        updateApprovalModeUI();
    }

    if (document.getElementById('policy_check_enabled')) {
        updateAdvertisingPolicyUI();
        document
            .getElementById('policy_check_enabled')
            .addEventListener('change', updateAdvertisingPolicyUI);
    }

    if (document.getElementById('order_name_template')) {
        updateNamingPreview();
    }
});
