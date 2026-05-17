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
