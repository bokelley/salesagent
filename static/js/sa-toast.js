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

    function dismiss(el) {
        el.classList.add('is-leaving');
        setTimeout(() => el.remove(), 240);
    }

    window.saToast = function (message, type) {
        type = type || 'info';
        const el = document.createElement('div');
        el.className = 'sa-toast sa-toast-' + type;
        el.setAttribute('role', type === 'error' ? 'alert' : 'status');
        const icons = { success: '✓', error: '✕', warning: '!', info: 'i' };
        el.innerHTML =
            '<span class="sa-toast__icon" aria-hidden="true">' + (icons[type] || '') + '</span>' +
            '<span class="sa-toast__msg">' + (message || '') + '</span>' +
            '<button type="button" class="sa-toast__close" aria-label="Dismiss">×</button>';
        el.querySelector('.sa-toast__close').addEventListener('click', () => dismiss(el));
        root.appendChild(el);
        setTimeout(() => { if (el.isConnected) dismiss(el); }, 4000);
        return el;
    };

    // saFetchAction(button, fetchFn, opts)
    //   button: HTMLElement — disabled + " …" suffix while in flight
    //   fetchFn: async () => Response — caller does the fetch
    //   opts: { successMessage?, errorPrefix?, reloadOnSuccess?, onSuccess? }
    window.saFetchAction = async function (button, fetchFn, opts) {
        opts = opts || {};
        const orig = button.innerHTML;
        button.disabled = true;
        button.dataset.saInFlight = '1';
        button.innerHTML = orig + ' …';
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
            button.innerHTML = orig;
            delete button.dataset.saInFlight;
        }
    };

    // Mirror Flask flash messages into toasts so the same UX surface
    // covers both code paths. The server-rendered banner stays for
    // accessibility + no-JS fallback; the toast is an additional cue.
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
