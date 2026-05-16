/**
 * Tenant Settings Page JavaScript
 *
 * Configuration is passed via data attributes on #settings-config element:
 * - data-script-name: Flask script_name for URL routing
 * - data-tenant-id: Current tenant ID
 * - data-active-adapter: Active adapter name
 * - data-a2a-port: A2A server port
 * - data-is-production: Production environment flag
 * - data-virtual-host: Virtual host for production
 * - data-subdomain: Subdomain for production
 */

// Get configuration from data attributes
const config = (function() {
    const configEl = document.getElementById('settings-config');
    if (!configEl) {
        console.error('Settings config element not found');
        return {};
    }

    return {
        scriptName: configEl.dataset.scriptName || '',
        tenantId: configEl.dataset.tenantId || '',
        tenantName: configEl.dataset.tenantName || '',
        activeAdapter: configEl.dataset.activeAdapter || '',
        a2aPort: configEl.dataset.a2aPort || '8091',
        mcpPort: configEl.dataset.mcpPort || '8080',
        isProduction: configEl.dataset.isProduction === 'true',
        isEmbedded: configEl.dataset.isEmbedded === 'true',
        virtualHost: configEl.dataset.virtualHost || '',
        subdomain: configEl.dataset.subdomain || '',
        salesAgentDomain: configEl.dataset.salesAgentDomain || 'sales-agent.example.com'
    };
})();

// Navigation
document.querySelectorAll('.settings-nav-item').forEach(item => {
    item.addEventListener('click', function(e) {
        // If this is a real link (has href attribute and no data-section), let it navigate
        const sectionId = this.dataset.section;
        if (!sectionId) {
            return; // Let the browser handle the navigation
        }

        e.preventDefault();

        // Update active nav
        document.querySelectorAll('.settings-nav-item').forEach(i => i.classList.remove('active'));
        this.classList.add('active');

        // Show corresponding section
        document.querySelectorAll('.settings-section').forEach(s => s.classList.remove('active'));
        const section = document.getElementById(sectionId);
        if (section) {
            section.classList.add('active');
        }

        // Update URL without reload
        history.pushState(null, '', `#${sectionId}`);
    });
});

// Load section from URL hash
window.addEventListener('load', function() {
    const hash = window.location.hash.substring(1);
    if (hash) {
        const navItem = document.querySelector(`[data-section="${hash}"]`);
        if (navItem) {
            navItem.click();
        }
    }
});

// Helper function to switch to a specific section
function switchSettingsSection(sectionId) {
    const navItem = document.querySelector(`[data-section="${sectionId}"]`);
    if (navItem) {
        navItem.click();
    }
}

// Copy to clipboard
function copyToClipboard(buttonOrText) {
    let textToCopy;
    let buttonElement;

    if (typeof buttonOrText === 'string') {
        // Direct text passed
        textToCopy = buttonOrText;
        buttonElement = event.target; // Get the button that was clicked
    } else {
        // Button element passed (existing behavior)
        textToCopy = buttonOrText.parentElement.querySelector('pre').textContent;
        buttonElement = buttonOrText;
    }

    navigator.clipboard.writeText(textToCopy).then(() => {
        const originalText = buttonElement.textContent;
        buttonElement.textContent = 'Copied!';
        setTimeout(() => {
            buttonElement.textContent = originalText;
        }, 2000);
    });
}

// Format JSON
function formatJSON() {
    const textarea = document.getElementById('raw_config');
    try {
        const json = JSON.parse(textarea.value);
        textarea.value = JSON.stringify(json, null, 2);
    } catch (e) {
        alert('Invalid JSON: ' + e.message);
    }
}

// Test Slack
function testSlack() {
    const webhookUrl = document.getElementById('slack_webhook_url').value;
    if (!webhookUrl) {
        alert('Please enter a webhook URL first');
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/test_slack`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            webhook_url: webhookUrl
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ Test notification sent successfully!');
        } else {
            alert('❌ Test failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Save adapter settings
function saveAdapter() {
    const adapterType = document.querySelector('select[name="adapter_type"]').value;

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            adapter_type: adapterType
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Adapter settings saved successfully!');
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Check OAuth status (for GAM)
function checkOAuthStatus() {
    fetch(`${config.scriptName}/api/oauth/status`)
        .then(response => response.json())
        .then(data => {
            const statusBadge = document.getElementById('oauth-status-badge');
            const statusText = document.getElementById('oauth-status-text');

            // Only update if elements exist (they may not be on all pages)
            if (!statusBadge || !statusText) {
                return;
            }

            if (data.authenticated) {
                statusBadge.textContent = 'Connected';
                statusBadge.className = 'badge badge-success';
                statusText.textContent = `Authenticated as ${data.user_email}`;
            } else {
                statusBadge.textContent = 'Not Connected';
                statusBadge.className = 'badge badge-danger';
                statusText.textContent = 'Not authenticated';
            }
        })
        .catch(error => {
            console.error('Error checking OAuth status:', error);
        });
}

// Initiate GAM OAuth
function initiateGAMAuth() {
    const tenantId = config.tenantId;
    const oauthUrl = `${config.scriptName}/auth/gam/authorize/${tenantId}`;

    // Open OAuth flow in popup
    const width = 600;
    const height = 700;
    const left = (screen.width - width) / 2;
    const top = (screen.height - height) / 2;

    const popup = window.open(
        oauthUrl,
        'GAM OAuth',
        `width=${width},height=${height},left=${left},top=${top}`
    );

    // Poll for completion and reload to show updated config
    const pollTimer = setInterval(() => {
        if (popup.closed) {
            clearInterval(pollTimer);
            location.reload();
        }
    }, 1000);
}

// Detect GAM network code
function detectGAMNetwork() {
    const button = document.querySelector('button[onclick="detectGAMNetwork()"]');
    const originalText = button.textContent;
    const refreshToken = document.getElementById('gam_refresh_token').value;

    if (!refreshToken) {
        alert('Please enter a refresh token first');
        return;
    }

    button.disabled = true;
    button.textContent = 'Detecting...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/detect-network`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            refresh_token: refreshToken
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            // Handle multiple networks - show dropdown for selection
            if (data.multiple_networks && data.networks) {
                showNetworkSelector(data.networks, refreshToken);
            } else {
                // Single network - auto-select
                document.getElementById('gam_network_code').value = data.network_code;

                // Update trafficker ID if provided
                if (data.trafficker_id) {
                    document.getElementById('gam_trafficker_id').value = data.trafficker_id;
                }

                // Store currency/timezone info for save
                window.gamDetectedCurrency = data.currency_code || 'USD';
                window.gamSecondaryCurrencies = data.secondary_currencies || [];
                window.gamDetectedTimezone = data.timezone || null;

                alert(`✅ Network code detected: ${data.network_code}`);
            }
        } else {
            alert('❌ ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Show network selector when multiple networks found
function showNetworkSelector(networks, refreshToken) {
    const container = document.createElement('div');
    container.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000;';

    const modal = document.createElement('div');
    modal.style.cssText = 'background: white; padding: 2rem; border-radius: 8px; max-width: 500px; width: 90%;';

    modal.innerHTML = `
        <h3 style="margin-top: 0;">Select GAM Network</h3>
        <p style="color: #666;">You have access to multiple GAM networks. Please select which one to use:</p>
        <select id="network-selector" class="form-control" style="margin: 1rem 0; padding: 0.5rem; font-size: 1rem;">
            ${networks.map(net => `
                <option value="${net.network_code}">
                    ${net.network_name} (${net.network_code})
                </option>
            `).join('')}
        </select>
        <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
            <button onclick="cancelNetworkSelection()" class="btn btn-secondary">Cancel</button>
            <button onclick="confirmNetworkSelection('${refreshToken}')" class="btn btn-primary">Confirm Selection</button>
        </div>
    `;

    container.appendChild(modal);
    document.body.appendChild(container);

    // Store networks data for later use
    window.gamNetworks = networks;
    window.networkSelectorContainer = container;
}

// Cancel network selection
function cancelNetworkSelection() {
    if (window.networkSelectorContainer) {
        window.networkSelectorContainer.remove();
        window.networkSelectorContainer = null;
        window.gamNetworks = null;
    }
}

// Confirm network selection and get trafficker ID
function confirmNetworkSelection(refreshToken) {
    const selector = document.getElementById('network-selector');
    const selectedNetworkCode = selector.value;
    const selectedNetwork = window.gamNetworks.find(n => n.network_code === selectedNetworkCode);

    if (!selectedNetwork) {
        alert('Error: Network not found');
        return;
    }

    // Close modal
    cancelNetworkSelection();

    // Get trafficker ID for selected network
    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/detect-network`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            refresh_token: refreshToken,
            network_code: selectedNetworkCode
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('gam_network_code').value = selectedNetworkCode;

            if (data.trafficker_id) {
                document.getElementById('gam_trafficker_id').value = data.trafficker_id;
            }

            // Store currency/timezone info for save (use the selected network's values)
            window.gamDetectedCurrency = selectedNetwork.currency_code || 'USD';
            window.gamSecondaryCurrencies = selectedNetwork.secondary_currencies || [];
            window.gamDetectedTimezone = selectedNetwork.timezone || null;

            alert(`✅ Network selected: ${selectedNetwork.network_name} (${selectedNetworkCode})`);
        } else {
            alert('❌ Error getting trafficker ID: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Save manually entered token
function saveManualToken() {
    const refreshToken = document.getElementById('gam_refresh_token').value;

    if (!refreshToken) {
        alert('Please enter a refresh token first');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Saving...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            adapter: 'google_ad_manager',
            gam_refresh_token: refreshToken
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            alert('✅ Token saved! Page will reload to show next steps.');
            location.reload();
        } else {
            alert('❌ Failed to save: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Save GAM configuration
function saveGAMConfig() {
    // Check if GAM form fields exist (defensive check for wrong adapter page)
    const networkCodeField = document.getElementById('gam_network_code');
    const refreshTokenField = document.getElementById('gam_refresh_token');
    const traffickerIdField = document.getElementById('gam_trafficker_id');

    if (!networkCodeField || !refreshTokenField) {
        console.error('GAM configuration fields not found - are you on the GAM adapter page?');
        alert('Error: GAM configuration fields not available. Please select Google Ad Manager adapter first.');
        return;
    }

    const networkCode = networkCodeField.value;
    const refreshToken = refreshTokenField.value;
    const traffickerId = traffickerIdField?.value || '';
    const orderNameTemplate = (document.getElementById('gam_order_name_template') || document.getElementById('order_name_template'))?.value || '';
    const lineItemNameTemplate = (document.getElementById('gam_line_item_name_template') || document.getElementById('line_item_name_template'))?.value || '';

    // Get detected currency/timezone info (stored when network was detected)
    const networkCurrency = window.gamDetectedCurrency || null;
    const secondaryCurrencies = window.gamSecondaryCurrencies || [];
    const networkTimezone = window.gamDetectedTimezone || null;

    if (!refreshToken) {
        alert('Please provide a Refresh Token');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Saving...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            adapter: 'google_ad_manager',
            gam_network_code: networkCode,
            gam_refresh_token: refreshToken,
            gam_trafficker_id: traffickerId,
            order_name_template: orderNameTemplate,
            line_item_name_template: lineItemNameTemplate,
            network_currency: networkCurrency,
            secondary_currencies: secondaryCurrencies,
            network_timezone: networkTimezone
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            alert('✅ GAM configuration saved successfully');
            location.reload();
        } else {
            alert('❌ Failed to save: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Refresh GAM info (currencies, etc.) from GAM API and save to config
function refreshGAMInfo() {
    const refreshToken = document.getElementById('gam_refresh_token').value;
    const networkCode = document.getElementById('gam_network_code').value;

    if (!refreshToken) {
        alert('Please configure OAuth first');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Refreshing...';

    // First, call test-connection to get current GAM network info
    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/test-connection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success && data.networks && data.networks.length > 0) {
            const network = data.networks[0];
            // Save updated currency/timezone info via configure endpoint
            // Note: test-connection returns currencyCode/secondaryCurrencyCodes/timeZone (camelCase from GAM API)
            return fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/configure`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    auth_method: 'oauth',
                    network_code: networkCode || network.networkCode,
                    refresh_token: refreshToken,
                    trafficker_id: document.getElementById('gam_trafficker_id')?.value,
                    network_currency: network.currencyCode,
                    secondary_currencies: network.secondaryCurrencyCodes || [],
                    network_timezone: network.timeZone
                })
            });
        }
        throw new Error(data.error || 'Failed to fetch GAM info');
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;
        if (data.success) {
            alert('GAM info refreshed successfully');
            location.reload();
        } else {
            alert('Failed to save: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('Error: ' + error.message);
    });
}

// Test GAM connection
function testGAMConnection() {
    const refreshToken = document.getElementById('gam_refresh_token').value;

    if (!refreshToken) {
        alert('Please provide a refresh token first');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Testing...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/test-connection`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            refresh_token: refreshToken
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            alert('✅ Connection successful!');
        } else {
            alert('❌ Connection failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Configure GAM
function configureGAM() {
    const form = document.getElementById('gam-config-form');
    const formData = new FormData(form);

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/configure`, {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ GAM configuration saved successfully!');
            location.reload();
        } else {
            alert('❌ Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}


// Edit GAM configuration (clear existing config to show form)
function editGAMConfig() {
    if (!confirm('This will allow you to reconfigure your GAM settings. Continue?')) {
        return;
    }

    // Clear network code from database to trigger form display
    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            adapter: 'google_ad_manager',
            gam_network_code: '',  // Clear to trigger reconfiguration
            action: 'edit_config'
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Reload page to show configuration form
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Check for in-progress sync on page load
function checkForInProgressSync() {
    // Only check if we're on a page with the sync button
    const button = document.querySelector('button[onclick="syncGAMInventory()"]');
    if (!button) return;

    // Check if there's a running sync
    const checkUrl = `${config.scriptName}/tenant/${config.tenantId}/gam/sync-status/latest`;

    fetch(checkUrl)
        .then(response => {
            if (response.ok) {
                return response.json();
            }
            // If no in-progress sync, that's fine - button stays as "Sync Now"
            return null;
        })
        .then(data => {
            if (data && data.status === 'running') {
                // Resume polling the existing sync
                const originalText = button.innerHTML;
                button.disabled = true;

                // Start loading animation
                let dots = '';
                button.innerHTML = '⏳ Syncing';
                const loadingInterval = setInterval(() => {
                    dots = dots.length >= 3 ? '' : dots + '.';
                    button.innerHTML = `⏳ Syncing${dots}`;
                }, 300);

                // Start polling the existing sync
                pollSyncStatus(data.sync_id, button, originalText, loadingInterval);
            }
        })
        .catch(error => {
            // Silently fail - user can manually start sync
            console.log('Could not check for in-progress sync:', error);
        });
}

// Sync GAM inventory (with background polling)
function syncGAMInventory(mode = 'full') {
    // Find the button that was clicked
    const button = mode === 'incremental'
        ? document.querySelector('button[onclick*="incremental"]')
        : document.querySelector('button[onclick*="full"]');

    if (!button) {
        alert('❌ Could not find sync button');
        return;
    }

    const originalText = button.innerHTML;

    // Disable both buttons during sync
    const allButtons = document.querySelectorAll('button[onclick*="syncGAMInventory"]');
    allButtons.forEach(btn => btn.disabled = true);

    // Simple animated dots loading indicator
    let dots = '';
    const syncLabel = mode === 'incremental' ? 'Syncing (Incremental)' : 'Syncing (Full Reset)';
    button.innerHTML = `⏳ ${syncLabel}`;
    const loadingInterval = setInterval(() => {
        dots = dots.length >= 3 ? '' : dots + '.';
        button.innerHTML = `⏳ ${syncLabel}${dots}`;
    }, 300);

    const url = `${config.scriptName}/api/tenant/${config.tenantId}/inventory/sync`;

    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ mode: mode })
    })
    .then(response => {
        // Handle 202 Accepted (sync started) or 400/409 (already running/error)
        if (response.status === 202 || response.ok || response.status === 409 || response.status === 400) {
            return response.json();
        }
        throw new Error(`Server error: ${response.status}`);
    })
    .then(data => {
        // New response format: {sync_id, status, message} or {error, sync_id}
        if (data.sync_id && (data.status === 'running' || data.status === 'in_progress')) {
            // Sync started or already running - poll for status
            pollSyncStatus(data.sync_id, button, originalText, loadingInterval);
        } else if (data.error) {
            // Error response
            clearInterval(loadingInterval);

            // Re-enable both buttons
            const allButtons = document.querySelectorAll('button[onclick*="syncGAMInventory"]');
            allButtons.forEach(btn => btn.disabled = false);

            button.innerHTML = originalText;
            alert('❌ Sync failed: ' + data.error);
        } else {
            // Unexpected response format
            clearInterval(loadingInterval);

            // Re-enable both buttons
            const allButtons = document.querySelectorAll('button[onclick*="syncGAMInventory"]');
            allButtons.forEach(btn => btn.disabled = false);

            button.innerHTML = originalText;
            alert('❌ Sync failed: ' + (data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        clearInterval(loadingInterval);

        // Re-enable both buttons
        const allButtons = document.querySelectorAll('button[onclick*="syncGAMInventory"]');
        allButtons.forEach(btn => btn.disabled = false);

        button.innerHTML = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Poll sync status until completion
function pollSyncStatus(syncId, button, originalText, loadingInterval) {
    const statusUrl = `${config.scriptName}/tenant/${config.tenantId}/gam/sync-status/${syncId}`;

    // Show "navigate away" message
    const syncMessage = document.createElement('div');
    syncMessage.id = 'sync-progress-message';
    syncMessage.className = 'alert alert-info mt-2';
    syncMessage.innerHTML = '<strong>💡 Tip:</strong> Feel free to navigate away - the sync continues in the background!';
    button.parentElement.appendChild(syncMessage);

    const checkStatus = () => {
        fetch(statusUrl)
            .then(response => response.json())
            .then(data => {
                // Update button text with progress
                if (data.progress) {
                    clearInterval(loadingInterval);
                    const progress = data.progress;
                    const phaseText = progress.phase || 'Syncing';
                    const count = progress.count || 0;
                    const phaseNum = progress.phase_num || 0;
                    const totalPhases = progress.total_phases || 6;

                    if (count > 0) {
                        button.innerHTML = `⏳ ${phaseText}: ${count} items (${phaseNum}/${totalPhases})`;
                    } else {
                        button.innerHTML = `⏳ ${phaseText} (${phaseNum}/${totalPhases})`;
                    }
                }

                if (data.status === 'completed') {
                    clearInterval(loadingInterval);

                    // Re-enable both sync buttons
                    const allButtons = document.querySelectorAll('button[onclick*="syncGAMInventory"]');
                    allButtons.forEach(btn => btn.disabled = false);

                    // Reset the button that was clicked
                    button.innerHTML = originalText;

                    // Remove progress message
                    const msg = document.getElementById('sync-progress-message');
                    if (msg) msg.remove();

                    // Show success message with summary
                    const summary = data.summary || {};
                    const adUnitCount = summary.ad_units?.total || 0;
                    const placementCount = summary.placements?.total || 0;
                    const labelCount = summary.labels?.total || 0;
                    const targetingKeyCount = summary.custom_targeting?.total_keys || 0;
                    const audienceCount = summary.audience_segments?.total || 0;

                    let message = `✅ Inventory synced successfully!\n\n`;
                    if (adUnitCount > 0) message += `• ${adUnitCount} ad units\n`;
                    if (placementCount > 0) message += `• ${placementCount} placements\n`;
                    if (labelCount > 0) message += `• ${labelCount} labels\n`;
                    if (targetingKeyCount > 0) message += `• ${targetingKeyCount} custom targeting keys\n`;
                    if (audienceCount > 0) message += `• ${audienceCount} audience segments\n`;

                    alert(message);
                    location.reload();
                } else if (data.status === 'failed') {
                    clearInterval(loadingInterval);
                    button.disabled = false;
                    button.innerHTML = originalText;

                    // Remove progress message
                    const msg = document.getElementById('sync-progress-message');
                    if (msg) msg.remove();

                    alert('❌ Sync failed: ' + (data.error || 'Unknown error'));
                } else if (data.status === 'running' || data.status === 'pending') {
                    // Still running - continue polling
                    setTimeout(checkStatus, 2000); // Poll every 2 seconds
                } else {
                    // Unknown status
                    clearInterval(loadingInterval);
                    button.disabled = false;
                    button.innerHTML = originalText;

                    // Remove progress message
                    const msg = document.getElementById('sync-progress-message');
                    if (msg) msg.remove();

                    alert('❌ Unknown sync status: ' + data.status);
                }
            })
            .catch(error => {
                clearInterval(loadingInterval);
                button.disabled = false;
                button.innerHTML = originalText;

                // Remove progress message
                const msg = document.getElementById('sync-progress-message');
                if (msg) msg.remove();

                alert('❌ Error checking sync status: ' + error.message);
            });
    };

    // Start polling after 1 second
    setTimeout(checkStatus, 1000);
}

// Reset a stuck sync job
function resetStuckSync() {
    if (!confirm('⚠️ This will mark the current sync as failed and allow you to start a new one.\n\nAre you sure you want to reset the stuck sync?')) {
        return;
    }

    const button = document.querySelector('button[onclick*="resetStuckSync"]');
    if (button) {
        button.disabled = true;
        button.innerHTML = '⏳ Resetting...';
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/reset-stuck-sync`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('✅ ' + data.message);
                location.reload();
            } else {
                alert('❌ Failed to reset sync: ' + (data.error || data.message || 'Unknown error'));
                if (button) {
                    button.disabled = false;
                    button.innerHTML = '🛑 Reset Stuck Sync';
                }
            }
        })
        .catch(error => {
            alert('❌ Error resetting sync: ' + error.message);
            if (button) {
                button.disabled = false;
                button.innerHTML = '🛑 Reset Stuck Sync';
            }
        });
}

// Check OAuth token status
function checkTokenStatus() {
    fetch(`${config.scriptName}/api/oauth/status`)
        .then(response => response.json())
        .then(data => {
            const statusDiv = document.getElementById('token-status');
            if (data.authenticated) {
                statusDiv.innerHTML = `
                    <div class="alert alert-success">
                        ✅ Authenticated as ${data.user_email}
                        <button onclick="revokeToken()" class="btn btn-sm btn-danger ml-2">Revoke</button>
                    </div>
                `;
            } else {
                statusDiv.innerHTML = `
                    <div class="alert alert-warning">
                        ⚠️ Not authenticated
                    </div>
                `;
            }
        });
}

// Generate A2A registration code
function generateA2ACode() {
    const agentUri = config.isProduction
        ? `https://${config.virtualHost}`
        : `http://localhost:${config.a2aPort}`;

    const agentUriAlt = config.isProduction
        ? `https://${config.subdomain}.${config.salesAgentDomain}`
        : `http://localhost:${config.a2aPort}`;

    const code = `
# A2A Registration Code
# Paste this into your AI agent's configuration

{
  "agent_uri": "${agentUri}",
  "protocol": "a2a",
  "version": "1.0"
}
    `.trim();

    document.getElementById('a2a-code-output').textContent = code;
}

// Toggle token visibility between truncated and full
function toggleTokenVisibility(button) {
    const tokenDisplay = button.parentElement.querySelector('.token-display');
    const isShowingFull = tokenDisplay.textContent === tokenDisplay.dataset.full;

    if (isShowingFull) {
        tokenDisplay.textContent = tokenDisplay.dataset.truncated;
        button.textContent = '👁';
        button.title = 'Show full token';
    } else {
        tokenDisplay.textContent = tokenDisplay.dataset.full;
        button.textContent = '👁‍🗨';
        button.title = 'Hide full token';
    }
}

// Copy access token to clipboard
function copyAccessToken(token) {
    const button = event.target.closest('button');
    if (!button) {
        alert('Failed to copy to clipboard');
        return;
    }

    const originalText = button.textContent;

    navigator.clipboard.writeText(token).then(() => {
        button.textContent = '✓';
        button.classList.add('btn-success');
        button.classList.remove('btn-outline-secondary');

        setTimeout(() => {
            button.textContent = originalText;
            button.classList.remove('btn-success');
            button.classList.add('btn-outline-secondary');
        }, 2000);
    }).catch(err => {
        alert('Failed to copy to clipboard: ' + err.message);
    });
}

// Delete principal
function deletePrincipal(principalId, principalName) {
    if (!confirm(`Are you sure you want to delete ${principalName}? This action cannot be undone.`)) {
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/principals/${principalId}/delete`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || `HTTP error ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            alert('Principal deleted successfully');
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Test signals endpoint
function testSignalsEndpoint() {
    const url = document.getElementById('signals_discovery_agent_uri').value;
    if (!url) {
        alert('Please enter a signals discovery agent URL first');
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/test_signals`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            url: url
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('✅ Connection successful! Found ' + data.signal_count + ' signals');
        } else {
            alert('❌ Connection failed: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('❌ Error: ' + error.message);
    });
}

// Debug log for adapter detection
// Update principal
function updatePrincipal(principalId) {
    const name = document.getElementById(`principal_name_${principalId}`).value;
    const advertiserIds = document.getElementById(`advertiser_ids_${principalId}`).value;

    fetch(`${config.scriptName}/tenant/${config.tenantId}/principal/${principalId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            name: name,
            advertiser_ids: advertiserIds
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Principal updated successfully');
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Fetch GAM advertisers for principal mapping
function fetchGAMAdvertisers() {
    const activeAdapter = config.activeAdapter;

    if (activeAdapter !== 'google_ad_manager') {
        alert('GAM advertiser sync is only available when Google Ad Manager adapter is active');
        return;
    }

    const button = event.target;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Fetching...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/api/gam/get-advertisers`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.textContent = originalText;

        if (data.success) {
            displayGAMAdvertisers(data.advertisers);
        } else {
            alert('❌ Failed to fetch advertisers: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.textContent = originalText;
        alert('❌ Error: ' + error.message);
    });
}

// Display GAM advertisers in a modal or section
function displayGAMAdvertisers(advertisers) {
    const container = document.getElementById('gam-advertisers-list');
    if (!container) {
        alert(`Found ${advertisers.length} advertisers. Check the console for details.`);
        console.table(advertisers);
        return;
    }

    container.innerHTML = '<h4>Available GAM Advertisers</h4>';
    const list = document.createElement('ul');
    list.className = 'list-group';

    advertisers.forEach(adv => {
        const item = document.createElement('li');
        item.className = 'list-group-item';
        item.innerHTML = `
            <strong>${adv.name}</strong>
            <br>
            <small class="text-muted">ID: ${adv.id}</small>
            <button class="btn btn-sm btn-primary float-right" onclick="selectAdvertiser('${adv.id}', '${adv.name}')">
                Select
            </button>
        `;
        list.appendChild(item);
    });

    container.appendChild(list);
    container.style.display = 'block';
}

// Select advertiser and populate form
function selectAdvertiser(advertiserId, advertiserName) {
    // Find the active principal form and populate it
    const activeForm = document.querySelector('.principal-form.active');
    if (activeForm) {
        const idField = activeForm.querySelector('[id^="advertiser_ids_"]');
        if (idField) {
            idField.value = advertiserId;
            alert(`Selected: ${advertiserName} (ID: ${advertiserId})`);
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Generate A2A code if section exists
    if (document.getElementById('a2a-code-output')) {
        generateA2ACode();
    }

    // Check for in-progress sync on page load
    checkForInProgressSync();
});

// Adapter selection functions (called from template onclick handlers)
function selectAdapter(adapterType) {
    // Save the adapter selection via API
    fetch(`${config.scriptName}/tenant/${config.tenantId}/settings/adapter`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            adapter: adapterType
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Reload to show the adapter's configuration
            location.reload();
        } else {
            alert('Error: ' + (data.error || data.message || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

function selectGAMAdapter() {
    selectAdapter('google_ad_manager');
}

// Copy A2A configuration to clipboard
function copyA2AConfig(principalId, principalName, accessToken) {
    // Capture the button element before async operations
    const button = event.target.closest('button');
    if (!button) {
        alert('Failed to copy to clipboard: Button element not found');
        return;
    }

    // Determine the A2A server URL (without /a2a suffix)
    let a2aUrl;
    if (config.isProduction) {
        // Production: Use subdomain or virtual host
        if (config.subdomain) {
            a2aUrl = `https://${config.subdomain}.${config.salesAgentDomain}`;
        } else if (config.virtualHost) {
            a2aUrl = `https://${config.virtualHost}`;
        } else {
            a2aUrl = `https://${config.salesAgentDomain}`;
        }
    } else {
        // Development: Use localhost with configured port
        a2aUrl = `http://localhost:${config.a2aPort}`;
    }

    // Create the A2A configuration JSON with name field
    const a2aConfig = {
        name: `${config.tenantName} - ${principalName}`,
        agent_uri: a2aUrl,
        protocol: "a2a",
        version: "1.0",
        auth: {
            type: "bearer",
            token: accessToken
        }
    };

    // Store original button state
    const originalText = button.textContent;

    // Copy to clipboard
    navigator.clipboard.writeText(JSON.stringify(a2aConfig, null, 2)).then(() => {
        // Show success feedback
        button.textContent = '✓ Copied!';
        button.classList.add('btn-success');
        button.classList.remove('btn-outline-primary');

        setTimeout(() => {
            button.textContent = originalText;
            button.classList.remove('btn-success');
            button.classList.add('btn-outline-primary');
        }, 2000);
    }).catch(err => {
        alert('Failed to copy to clipboard: ' + err.message);
    });
}

// Copy MCP configuration to clipboard
function copyMCPConfig(principalId, principalName, accessToken) {
    // Capture the button element before async operations
    const button = event.target.closest('button');
    if (!button) {
        alert('Failed to copy to clipboard: Button element not found');
        return;
    }

    // Determine the MCP server URL
    let mcpUrl;
    if (config.isProduction) {
        // Production: Use subdomain or virtual host
        if (config.subdomain) {
            mcpUrl = `https://${config.subdomain}.${config.salesAgentDomain}/mcp`;
        } else if (config.virtualHost) {
            mcpUrl = `https://${config.virtualHost}/mcp`;
        } else {
            mcpUrl = `https://${config.salesAgentDomain}/mcp`;
        }
    } else {
        // Development: Use localhost with configured MCP port
        mcpUrl = `http://localhost:${config.mcpPort}/mcp`;
    }

    // Create the MCP configuration JSON with name field
    const mcpConfig = {
        name: `${config.tenantName} - ${principalName}`,
        agent_uri: mcpUrl,
        protocol: "mcp",
        version: "1.0",
        auth: {
            type: "bearer",
            token: accessToken
        }
    };

    // Store original button state
    const originalText = button.textContent;

    // Copy to clipboard
    navigator.clipboard.writeText(JSON.stringify(mcpConfig, null, 2)).then(() => {
        // Show success feedback
        button.textContent = '✓ Copied!';
        button.classList.add('btn-success');
        button.classList.remove('btn-outline-primary');

        setTimeout(() => {
            button.textContent = originalText;
            button.classList.remove('btn-success');
            button.classList.add('btn-outline-primary');
        }, 2000);
    }).catch(err => {
        alert('Failed to copy to clipboard: ' + err.message);
    });
}

// Edit principal platform mappings
function editPrincipalMappings(principalId, principalName) {
    // Update modal title
    document.getElementById('editPrincipalModalTitle').textContent = `Edit Platform Mappings - ${principalName}`;

    // Store the principal ID for later use when saving
    document.getElementById('saveMappingsBtn').dataset.principalId = principalId;

    // Fetch current principal configuration
    fetch(`${config.scriptName}/tenant/${config.tenantId}/principal/${principalId}`, {
        method: 'GET',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            displayPrincipalMappingsForm(data.principal);
            // Show the modal using Bootstrap
            const modal = new bootstrap.Modal(document.getElementById('editPrincipalModal'));
            modal.show();
        } else {
            alert('Error loading principal: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Display the principal mappings form
function displayPrincipalMappingsForm(principal) {
    const formContainer = document.getElementById('editPrincipalForm');
    const platformMappings = principal.platform_mappings || {};

    let formHtml = '<div class="mb-3"><p class="text-muted">Configure how this advertiser maps to your ad server platforms.</p></div>';

    // GAM mapping
    const gamMapping = platformMappings.google_ad_manager || {};
    formHtml += `
        <div class="mb-3">
            <label class="form-label"><strong>Google Ad Manager</strong></label>
            <div class="form-check mb-2">
                <input class="form-check-input" type="checkbox" id="gam_enabled" ${gamMapping.enabled ? 'checked' : ''}>
                <label class="form-check-label" for="gam_enabled">
                    Enable GAM integration
                </label>
            </div>
            <div id="gam_config" style="${gamMapping.enabled ? '' : 'display: none;'}">
                <label for="gam_advertiser_select" class="form-label">GAM Advertiser</label>
                <select class="form-select" id="gam_advertiser_select" style="width: 100%;">
                    ${gamMapping.advertiser_id ? `<option value="${gamMapping.advertiser_id}" selected>${gamMapping.advertiser_id}</option>` : ''}
                </select>
                <small class="form-text text-muted">Search for a GAM advertiser by name or ID</small>
                <input type="hidden" id="gam_advertiser_id" value="${gamMapping.advertiser_id || ''}">
            </div>
        </div>
        <hr>
    `;

    // Mock mapping
    const mockMapping = platformMappings.mock || {};
    formHtml += `
        <div class="mb-3">
            <label class="form-label"><strong>Mock Adapter (Testing)</strong></label>
            <div class="form-check mb-2">
                <input class="form-check-input" type="checkbox" id="mock_enabled" ${mockMapping.enabled ? 'checked' : ''}>
                <label class="form-check-label" for="mock_enabled">
                    Enable Mock adapter for testing
                </label>
            </div>
        </div>
    `;

    formContainer.innerHTML = formHtml;

    // Add event listener to toggle GAM config visibility
    document.getElementById('gam_enabled').addEventListener('change', function() {
        document.getElementById('gam_config').style.display = this.checked ? 'block' : 'none';
    });

    // Initialize Select2 for GAM advertiser dropdown (same as create_principal.html)
    const selectElement = $('#gam_advertiser_select');
    selectElement.select2({
        placeholder: 'Search for a GAM Advertiser...',
        allowClear: true,
        minimumInputLength: 0,
        ajax: {
            url: `${config.scriptName}/tenant/${config.tenantId}/api/gam/get-advertisers`,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            delay: 250,
            data: function(params) {
                return JSON.stringify({
                    search: params.term || '',
                    limit: 100,
                    fetch_all: false
                });
            },
            processResults: function(data) {
                if (data.error) {
                    return { results: [] };
                }
                const results = data.advertisers.map(advertiser => ({
                    id: advertiser.id,
                    text: advertiser.name
                }));
                return { results: results };
            },
            transport: function(params, success, failure) {
                const request = fetch(params.url, {
                    method: params.type,
                    headers: params.headers,
                    credentials: 'same-origin',
                    body: params.data
                });
                request.then(response => response.json()).then(success).catch(failure);
                return request;
            }
        }
    });

    // Update hidden field when selection changes
    selectElement.on('select2:select', function(e) {
        document.getElementById('gam_advertiser_id').value = e.params.data.id;
    });
    selectElement.on('select2:clear', function() {
        document.getElementById('gam_advertiser_id').value = '';
    });
}

// Save principal platform mappings
function savePrincipalMappings() {
    const principalId = document.getElementById('saveMappingsBtn').dataset.principalId;

    // Build the platform mappings from form
    const platformMappings = {};

    // GAM mapping
    const gamEnabledField = document.getElementById('gam_enabled');
    if (gamEnabledField && gamEnabledField.checked) {
        const gamAdvertiserIdField = document.getElementById('gam_advertiser_id');
        if (!gamAdvertiserIdField) {
            console.error('GAM advertiser ID field not found');
            alert('Error: GAM configuration fields not available');
            return;
        }

        const gamAdvertiserId = gamAdvertiserIdField.value.trim();
        if (gamAdvertiserId) {
            platformMappings.google_ad_manager = {
                advertiser_id: gamAdvertiserId,
                enabled: true
            };
        } else {
            alert('Please enter a GAM Advertiser ID or disable GAM integration');
            return;
        }
    }

    // Mock mapping
    const mockEnabled = document.getElementById('mock_enabled').checked;
    if (mockEnabled) {
        platformMappings.mock = {
            advertiser_id: `mock_${principalId}`,
            enabled: true
        };
    }

    // Save via API
    fetch(`${config.scriptName}/tenant/${config.tenantId}/principal/${principalId}/update_mappings`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            platform_mappings: platformMappings
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Platform mappings updated successfully');
            // Close modal
            const modal = bootstrap.Modal.getInstance(document.getElementById('editPrincipalModal'));
            modal.hide();
            // Reload page to show updated mappings
            location.reload();
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
    });
}

// Service Account Management Functions
function createServiceAccount() {
    const button = document.getElementById('create-service-account-btn');
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Creating...';

    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/create-service-account`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Service account created successfully!\n\nEmail: ' + data.service_account_email + '\n\n' + data.message);
            // Reload page to show the service account email and next steps
            location.reload();
        } else {
            alert('Error creating service account: ' + (data.error || 'Unknown error'));
            button.disabled = false;
            button.innerHTML = '🔑 Create Service Account';
        }
    })
    .catch(error => {
        alert('Error: ' + error.message);
        button.disabled = false;
        button.innerHTML = '🔑 Create Service Account';
    });
}

function copyServiceAccountEmail() {
    const emailElement = document.querySelector('code');
    if (emailElement) {
        const email = emailElement.textContent;
        navigator.clipboard.writeText(email).then(() => {
            const button = event.target;
            const originalText = button.textContent;
            button.textContent = '✓ Copied!';
            button.classList.add('btn-success');
            button.classList.remove('btn-secondary');
            setTimeout(() => {
                button.textContent = originalText;
                button.classList.remove('btn-success');
                button.classList.add('btn-secondary');
            }, 2000);
        });
    }
}

function saveServiceAccountNetworkCode() {
    const button = event.target;
    const networkCodeInput = document.getElementById('service_account_network_code');
    const networkCode = networkCodeInput.value.trim();

    if (!networkCode) {
        alert('Please enter a network code');
        return;
    }

    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...';

    // Save network code via the GAM configure endpoint
    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/configure`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            auth_method: 'service_account',
            network_code: networkCode
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.innerHTML = 'Save Network Code';

        if (data.success) {
            alert('✅ Network code saved successfully!\n\nYou can now test the connection.');
            // Reload page to show updated state
            location.reload();
        } else {
            alert('❌ Failed to save network code:\n\n' + (data.error || data.errors?.join('\n') || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.innerHTML = 'Save Network Code';
        alert('Error: ' + error.message);
    });
}

function saveManualServiceAccount() {
    const button = event.target;
    const jsonInput = document.getElementById('manual_service_account_json');
    const networkCodeInput = document.getElementById('manual_network_code');
    const jsonText = jsonInput.value.trim();
    const networkCode = networkCodeInput.value.trim();

    if (!jsonText) {
        alert('Please paste your service account JSON key');
        return;
    }

    // Validate JSON
    let jsonData;
    try {
        jsonData = JSON.parse(jsonText);
    } catch (e) {
        alert('Invalid JSON format. Please paste the complete contents of your service account JSON key file.');
        return;
    }

    // Validate required fields in the service account JSON
    if (!jsonData.client_email || !jsonData.private_key) {
        alert('Invalid service account JSON. Make sure it contains "client_email" and "private_key" fields.');
        return;
    }

    if (!networkCode) {
        alert('Please enter a GAM network code');
        return;
    }

    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...';

    // Save service account JSON and network code
    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/configure`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            auth_method: 'service_account',
            service_account_json: jsonText,
            network_code: networkCode
        })
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.innerHTML = 'Save Service Account Configuration';

        if (data.success) {
            alert('✅ Service account configuration saved!\n\nNext, authorize the service account email (' + jsonData.client_email + ') in GAM:\n\n1. In GAM, go to Admin → Global Settings\n2. Scroll to API access and ensure it is enabled\n3. Click "Add a service account user"\n4. Paste the email and assign the Trafficker role\n5. Click Save\n\nNote: service accounts must be added through Global Settings → API access, not the Users page (which sends an email invitation a service account cannot accept). You can verify the account appears under Admin → Access & authorization → Users once added.\n\nThen come back here and test the connection.');
            location.reload();
        } else {
            alert('❌ Failed to save configuration:\n\n' + (data.error || data.errors?.join('\n') || 'Unknown error'));
        }
    })
    .catch(error => {
        button.disabled = false;
        button.innerHTML = 'Save Service Account Configuration';
        alert('Error: ' + error.message);
    });
}

function testGAMServiceAccountConnection() {
    const button = event.target;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Testing...';

    // Use existing GAM test connection endpoint
    // The backend will automatically use service account if configured
    fetch(`${config.scriptName}/tenant/${config.tenantId}/gam/test-connection`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        button.disabled = false;
        button.innerHTML = 'Test Connection';

        if (data.success) {
            alert('✅ Connection successful!\n\nNetwork: ' + (data.networks?.[0]?.displayName || 'N/A') + '\nNetwork Code: ' + (data.networks?.[0]?.networkCode || 'N/A'));
        } else {
            alert('❌ Connection failed!\n\n' + (data.error || 'Unknown error') + '\n\nPlease make sure:\n1. You authorized the service account in GAM under Admin → Global Settings → API access → "Add a service account user" (NOT through the Users page — that path sends an email invitation and will not work for service accounts)\n2. The service account now appears under Admin → Access & authorization → Users as an active user (this is how you verify the authorization took effect)\n3. You assigned the Trafficker role\n4. You clicked Save in GAM\n5. The network code is correct');
        }
    })
    .catch(error => {
        button.disabled = false;
        button.innerHTML = 'Test Connection';
        alert('Error: ' + error.message);
    });
}

// Currency Management Functions


