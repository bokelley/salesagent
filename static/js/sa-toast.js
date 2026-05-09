// sa-toast — corner-of-screen feedback for AJAX/fetch actions in the
// admin UI. Three pieces:
//
//   1. window.saToast(message, type) — shows a dismissible toast.
//      type ∈ {'success','error','info','warning'}. Auto-hides at 4s.
//
//   2. window.saFetchAction(button, fetchFn, opts) — wraps a click
//      handler: disables the button while in flight, shows a success or
//      error toast on completion, optionally reloads the page.
//
//   3. Server-rendered Flask flash messages get mirrored into toasts at
//      page load so AJAX and form-POST round trips share one UX surface.
(function () {
    const root = document.getElementById('sa-toast-root');
    if (!root) return;

    const ICONS = { success: '✓', error: '✕', warning: '!', info: 'i' };

    function dismiss(el) {
        if (el.dataset.saLeaving) return;
        el.dataset.saLeaving = '1';
        if (el._saTimer) {
            clearTimeout(el._saTimer);
            el._saTimer = null;
        }
        el.classList.add('is-leaving');
        setTimeout(() => el.remove(), 240);
    }

    window.saToast = function (message, type) {
        type = ICONS[type] ? type : 'info';
        const el = document.createElement('div');
        el.className = 'sa-toast sa-toast-' + type;
        // Assertive for errors so SR users hear them immediately; the root
        // is polite, and per-element live overrides the ancestor region.
        el.setAttribute('role', type === 'error' ? 'alert' : 'status');
        el.setAttribute('aria-live', type === 'error' ? 'assertive' : 'polite');

        const icon = document.createElement('span');
        icon.className = 'sa-toast__icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = ICONS[type];

        // textContent on the message span — never innerHTML — so server-derived
        // strings (saFetchAction body.error, mirrored flash text) cannot inject
        // markup. This is the security-critical line; do not change to innerHTML.
        const msg = document.createElement('span');
        msg.className = 'sa-toast__msg';
        msg.textContent = message == null ? '' : String(message);

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'sa-toast__close';
        close.setAttribute('aria-label', 'Dismiss');
        close.textContent = '×';
        close.addEventListener('click', () => dismiss(el));

        el.appendChild(icon);
        el.appendChild(msg);
        el.appendChild(close);
        root.appendChild(el);

        el._saTimer = setTimeout(() => { if (el.isConnected) dismiss(el); }, 4000);
        return el;
    };

    // Esc dismisses the most recent toast — keyboard users get parity with
    // mouse users who can click the × button.
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        const toasts = root.querySelectorAll('.sa-toast:not(.is-leaving)');
        const last = toasts[toasts.length - 1];
        if (last) dismiss(last);
    });

    // saFetchAction(button, fetchFn, opts)
    //   button: HTMLButtonElement — disabled + " …" suffix while in flight.
    //   fetchFn: async () => Response — caller does the fetch.
    //   opts: { successMessage?, errorPrefix?, reloadOnSuccess?, onSuccess? }
    window.saFetchAction = async function (button, fetchFn, opts) {
        opts = opts || {};
        if (button.dataset.saInFlight === '1') {
            // Re-entry guard — covers anchors and divs that ignore `disabled`.
            return { ok: false, error: new Error('action already in flight') };
        }
        const origText = button.textContent;
        button.disabled = true;
        button.dataset.saInFlight = '1';
        button.textContent = origText + ' …';
        try {
            const res = await fetchFn();
            let body = null;
            try { body = await res.clone().json(); } catch (_e) { body = null; }
            if (!res.ok || (body && body.success === false)) {
                const msg = (body && (body.error || body.message)) || (res.status + ' ' + res.statusText);
                window.saToast((opts.errorPrefix || 'Failed') + ': ' + msg, 'error');
                return { ok: false, body, response: res };
            }
            if (opts.successMessage) window.saToast(opts.successMessage, 'success');
            if (typeof opts.onSuccess === 'function') opts.onSuccess(body, res);
            if (opts.reloadOnSuccess) {
                setTimeout(() => window.location.reload(), 350);
            }
            return { ok: true, body, response: res };
        } catch (err) {
            window.saToast((opts.errorPrefix || 'Network error') + ': ' + (err.message || err), 'error');
            return { ok: false, error: err };
        } finally {
            button.disabled = false;
            button.textContent = origText;
            delete button.dataset.saInFlight;
        }
    };

    // Mirror Flask flash messages into toasts so the same UX surface covers
    // both code paths. textContent on read AND on render (saToast uses
    // textContent for the message span) — no HTML round-trip.
    document.querySelectorAll('.flash-messages .alert').forEach((el) => {
        const text = (el.textContent || '').replace(/×\s*$/, '').trim();
        if (!text) return;
        let type = 'info';
        if (el.classList.contains('alert-success')) type = 'success';
        else if (el.classList.contains('alert-error') || el.classList.contains('alert-danger')) type = 'error';
        else if (el.classList.contains('alert-warning')) type = 'warning';
        window.saToast(text, type);
    });
})();
