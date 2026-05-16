/**
 * Integrations page — Sprint 7 Phase 2.
 *
 * Extracted from tenant_settings.js when this section promoted out of
 * Tenant Settings into a Configure → Workspace peer page. The AI Services
 * subsection keeps its inline ``<script>`` block in the template
 * (templates/integrations.html) because it depends on Jinja-rendered
 * values like ``{{ current_provider }}`` and ``{{ current_model }}``.
 *
 * This file only carries the Slack ``testSlack`` helper today. Reads
 * config from #settings-config data attributes.
 */

const config = (function () {
    const el = document.getElementById('settings-config');
    if (!el) {
        console.error('Integrations config element not found');
        return {};
    }
    return {
        scriptName: el.dataset.scriptName || '',
        tenantId: el.dataset.tenantId || '',
    };
})();

function testSlack() {
    const webhookUrl = document.getElementById('slack_webhook_url').value;
    if (!webhookUrl) {
        alert('Please enter a webhook URL first');
        return;
    }

    fetch(`${config.scriptName}/tenant/${config.tenantId}/test_slack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ webhook_url: webhookUrl }),
    })
        .then((response) => response.json())
        .then((data) => {
            if (data.success) {
                alert('✅ Test notification sent successfully!');
            } else {
                alert('❌ Test failed: ' + (data.error || data.message || 'Unknown error'));
            }
        })
        .catch((error) => {
            alert('❌ Error: ' + error.message);
        });
}
