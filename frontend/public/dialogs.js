/* =========================================================
 * Canonical dialog primitives — ISSUE-085 / AUDIT-005 §Decision 10
 * Visual contract: docs/styleguide.html §5
 *
 * window.confirmDialog({title, message, okLabel, cancelLabel, danger})
 *   → Promise<boolean>
 * window.alertDialog({title, message, icon})
 *   → Promise<void>
 * window.toast({message, severity})
 *   severity: "info" | "success" | "warning" | "danger"
 *
 * Plain script (no module). Mount on every page except login.html.
 * ========================================================= */
(function () {
  'use strict';

  function ensureToastStack() {
    let stack = document.getElementById('toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toast-stack';
      document.body.appendChild(stack);
    }
    return stack;
  }

  function focusTrap(container) {
    const focusables = container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusables.length) return () => {};
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    function onKey(e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    container.addEventListener('keydown', onKey);
    return () => container.removeEventListener('keydown', onKey);
  }

  function buildDialog({ title, message, okLabel, cancelLabel, danger, icon, isAlert }) {
    const backdrop = document.createElement('div');
    backdrop.className = 'dialog-backdrop';
    backdrop.setAttribute('role', 'presentation');

    const dialog = document.createElement('div');
    dialog.className = 'dialog';
    dialog.setAttribute('role', isAlert ? 'alertdialog' : 'dialog');
    dialog.setAttribute('aria-modal', 'true');

    const h3 = document.createElement('h3');
    if (danger) h3.style.color = 'var(--color-danger)';
    h3.textContent = (icon ? icon + ' ' : '') + (title || '');

    const p = document.createElement('p');
    p.textContent = message || '';

    const actions = document.createElement('div');
    actions.className = 'dialog-actions';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-md btn-secondary';
    cancelBtn.textContent = cancelLabel || 'Cancel';

    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'btn btn-md ' + (danger ? 'btn-danger' : 'btn-primary');
    okBtn.textContent = okLabel || (isAlert ? 'OK' : 'Confirm');

    if (!isAlert) actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);

    dialog.appendChild(h3);
    if (message) dialog.appendChild(p);
    dialog.appendChild(actions);
    backdrop.appendChild(dialog);

    return { backdrop, dialog, okBtn, cancelBtn };
  }

  window.confirmDialog = function (opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      const previouslyFocused = document.activeElement;
      const { backdrop, dialog, okBtn, cancelBtn } = buildDialog({
        title: opts.title,
        message: opts.message,
        okLabel: opts.okLabel || 'Confirm',
        cancelLabel: opts.cancelLabel || 'Cancel',
        danger: !!opts.danger,
        isAlert: false,
      });

      function close(result) {
        document.removeEventListener('keydown', onEsc);
        releaseTrap();
        backdrop.remove();
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        resolve(result);
      }
      function onEsc(e) { if (e.key === 'Escape') close(false); }

      okBtn.addEventListener('click', () => close(true));
      cancelBtn.addEventListener('click', () => close(false));
      backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(false); });
      document.addEventListener('keydown', onEsc);

      document.body.appendChild(backdrop);
      const releaseTrap = focusTrap(dialog);
      setTimeout(() => okBtn.focus(), 0);
    });
  };

  window.alertDialog = function (opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      const previouslyFocused = document.activeElement;
      const { backdrop, dialog, okBtn } = buildDialog({
        title: opts.title,
        message: opts.message,
        okLabel: 'OK',
        icon: opts.icon || '',
        isAlert: true,
      });

      function close() {
        document.removeEventListener('keydown', onEsc);
        releaseTrap();
        backdrop.remove();
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        resolve();
      }
      function onEsc(e) { if (e.key === 'Escape') close(); }

      okBtn.addEventListener('click', close);
      backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
      document.addEventListener('keydown', onEsc);

      document.body.appendChild(backdrop);
      const releaseTrap = focusTrap(dialog);
      setTimeout(() => okBtn.focus(), 0);
    });
  };

  /* =========================================================
   * One-off modal behavior — FLAG-062
   * Normalizes Escape-to-close, backdrop-click-to-close, and
   * focus-first-interactive across markup-driven modals
   * (those defined in HTML and toggled via .hidden).
   *
   * Markup contract: add data-modal to the backdrop element. To
   * route close through a page-specific cleanup function, also
   * add data-modal-close="globalFunctionName". Without it the
   * default close is `el.classList.add('hidden')`.
   * ========================================================= */
  function firstFocusable(container) {
    const sel = 'input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])';
    return container.querySelector(sel);
  }

  window.attachModalBehavior = function (el, opts) {
    if (!el || el._modalBehaviorAttached) return;
    opts = opts || {};
    el._modalBehaviorAttached = true;
    const close = typeof opts.onClose === 'function'
      ? opts.onClose
      : () => el.classList.add('hidden');

    el.addEventListener('click', (e) => {
      if (el.classList.contains('hidden')) return;
      if (e.target === el) close();
    });

    let prevFocus = null;
    const sync = () => {
      const visible = !el.classList.contains('hidden');
      if (visible && !el._modalOpen) {
        el._modalOpen = true;
        prevFocus = document.activeElement;
        const f = firstFocusable(el);
        if (f) setTimeout(() => f.focus(), 0);
      } else if (!visible && el._modalOpen) {
        el._modalOpen = false;
        if (prevFocus && prevFocus.focus) prevFocus.focus();
      }
    };
    new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ['class'] });
    sync();

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && el._modalOpen) close();
    });
  };

  function autoRegisterModals() {
    document.querySelectorAll('[data-modal]').forEach((el) => {
      const fnName = el.getAttribute('data-modal-close');
      const onClose = fnName && typeof window[fnName] === 'function'
        ? window[fnName]
        : null;
      window.attachModalBehavior(el, onClose ? { onClose } : {});
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoRegisterModals);
  } else {
    autoRegisterModals();
  }

  const TOAST_ICONS = { info: 'ℹ️', success: '✅', warning: '⚠️', danger: '❌' };
  const SEVERITY_ALIASES = { error: 'danger', warn: 'warning' };

  window.toast = function (opts) {
    // Allow plain string for ergonomics
    if (typeof opts === 'string') opts = { message: opts };
    opts = opts || {};
    const stack = ensureToastStack();

    // Cap at 3 stacked — drop oldest
    while (stack.children.length >= 3) stack.firstChild.remove();

    let severity = opts.severity || 'info';
    severity = SEVERITY_ALIASES[severity] || severity;
    if (!TOAST_ICONS[severity]) severity = 'info';

    const el = document.createElement('div');
    el.className = 'toast toast-' + severity;
    el.setAttribute('role', severity === 'danger' || severity === 'warning' ? 'alert' : 'status');
    el.textContent = TOAST_ICONS[severity] + ' ' + (opts.message || '');

    stack.appendChild(el);
    setTimeout(() => {
      el.classList.add('toast-leaving');
      setTimeout(() => el.remove(), 200);
    }, 3500);
  };
})();
