/* =========================================================
 * On-screen virtual keyboard — FEATURE-043
 * Touch kiosk has no physical keyboard. This mounts a single
 * dismissible keyboard that types into the focused field.
 *
 * Visual contract: authored component CSS in shared-styles.css
 * (.osk*) — NOT Tailwind utilities, so no dead-class exposure.
 *
 * TRIGGER (reported in commit): the keyboard is gated to coarse-
 * pointer / touch hardware. It auto-shows when an eligible field
 * gains focus AND the device is touch-capable:
 *     matchMedia('(pointer: coarse)') || navigator.maxTouchPoints > 0
 * Admin-over-Tailscale (laptop, fine pointer + real keyboard)
 * never sees it. Override either way with localStorage 'osk':
 *     localStorage.osk = 'on'   → force-enable (e.g. kiosk w/ mouse)
 *     localStorage.osk = 'off'  → force-disable
 * When enabled, <body> gets the `osk-enabled` class.
 *
 * Behaviour:
 *   - Numeric pad for number/tel/decimal fields + [data-osk="numeric"].
 *   - Full QWERTY (with number row + symbols layer) for text fields.
 *   - Never steals focus (pointerdown is prevented) → no focus trap.
 *   - Dismissible: Hide key, Escape, or focus leaving to a non-field.
 *   - Opt a field out entirely with [data-osk="off"].
 *
 * Plain script (no module). Mount on EVERY page incl. login.html.
 * ========================================================= */
(function () {
  'use strict';

  // ── Enablement / trigger ────────────────────────────────────────────
  function isEnabled() {
    var pref = null;
    try { pref = localStorage.getItem('osk'); } catch (e) {}
    if (pref === 'on') return true;
    if (pref === 'off') return false;
    var coarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    return !!coarse || (navigator.maxTouchPoints || 0) > 0;
  }

  if (!isEnabled()) return;
  document.addEventListener('DOMContentLoaded', function () {
    document.body.classList.add('osk-enabled');
  });

  // ── Field eligibility / layout selection ────────────────────────────
  var SKIP_TYPES = ['checkbox', 'radio', 'hidden', 'file', 'range', 'color',
                    'date', 'datetime-local', 'month', 'week', 'time', 'submit',
                    'button', 'reset', 'image'];
  // Types whose .value supports selectionStart/setSelectionRange.
  var SELECTABLE = ['text', 'search', 'password', 'tel', 'url', 'email'];

  function fieldType(el) { return (el.getAttribute('type') || 'text').toLowerCase(); }

  function isEligible(el) {
    if (!el || el._oskKey) return false;
    var tag = el.tagName;
    if (tag === 'TEXTAREA') return !el.disabled && !el.readOnly && el.dataset.osk !== 'off';
    if (tag !== 'INPUT') return false;
    if (el.disabled || el.readOnly) return false;
    if (el.dataset.osk === 'off') return false;
    return SKIP_TYPES.indexOf(fieldType(el)) === -1;
  }

  function wantsNumeric(el) {
    if (el.dataset.osk === 'numeric') return true;
    if (el.tagName !== 'INPUT') return false;
    var t = fieldType(el);
    if (t === 'number' || t === 'tel') return true;
    var im = (el.getAttribute('inputmode') || '').toLowerCase();
    return im === 'numeric' || im === 'decimal';
  }

  function allowsDecimal(el) {
    if (el.dataset.osk === 'numeric' && el.dataset.oskDecimal === 'false') return false;
    var im = (el.getAttribute('inputmode') || '').toLowerCase();
    if (im === 'numeric') return false;          // explicit integer
    if (fieldType(el) === 'tel') return false;   // phone: digits only
    return true;
  }

  function canSelect(el) {
    if (el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && SELECTABLE.indexOf(fieldType(el)) !== -1;
  }

  // ── Text mutation (cursor-aware where supported) ────────────────────
  function fireInput(el) {
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function insertText(el, ch) {
    if (wantsNumeric(el)) {
      if (ch === '.') {
        if (!allowsDecimal(el)) return;
      } else if (!/[0-9]/.test(ch)) {
        return;
      }
      // type=number sanitizes a transient "12." back to "" on assignment, which
      // would drop the integer part when entering amounts. Keep the true typed
      // string in a shadow buffer (synced from .value on focus) and mirror it.
      if (!canSelect(el)) {
        var buf = (el._oskBuf != null ? el._oskBuf : (el.value || ''));
        if (ch === '.' && buf.indexOf('.') !== -1) return;
        buf += ch;
        el._oskBuf = buf;
        el.value = buf;
        fireInput(el);
        return;
      }
      if (ch === '.' && el.value.indexOf('.') !== -1) return;
    }
    if (canSelect(el) && el.selectionStart !== null && el.selectionStart !== undefined) {
      var s = el.selectionStart, e = el.selectionEnd;
      el.value = el.value.slice(0, s) + ch + el.value.slice(e);
      var pos = s + ch.length;
      try { el.setSelectionRange(pos, pos); } catch (err) {}
    } else {
      el.value = el.value + ch;
    }
    fireInput(el);
  }

  function backspace(el) {
    if (wantsNumeric(el) && !canSelect(el)) {
      var buf = (el._oskBuf != null ? el._oskBuf : (el.value || '')).slice(0, -1);
      el._oskBuf = buf;
      el.value = buf;
      fireInput(el);
      return;
    }
    if (canSelect(el) && el.selectionStart !== null && el.selectionStart !== undefined) {
      var s = el.selectionStart, e = el.selectionEnd;
      if (s === e && s > 0) s -= 1;
      el.value = el.value.slice(0, s) + el.value.slice(e);
      try { el.setSelectionRange(s, s); } catch (err) {}
    } else {
      el.value = el.value.slice(0, -1);
    }
    fireInput(el);
  }

  function submitOrAdvance(el) {
    if (el.tagName === 'TEXTAREA') { insertText(el, '\n'); return; }
    var form = el.form;
    if (form && typeof form.requestSubmit === 'function') {
      form.requestSubmit();
    } else if (form && typeof form.submit === 'function') {
      // Fallback: trigger a synthetic submit so listeners run.
      if (form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))) {
        form.submit();
      }
    } else {
      el.blur();
      hide();
    }
  }

  // ── Keyboard layouts ────────────────────────────────────────────────
  var LETTERS = [
    ['1','2','3','4','5','6','7','8','9','0'],
    ['q','w','e','r','t','y','u','i','o','p'],
    ['a','s','d','f','g','h','j','k','l'],
    ['z','x','c','v','b','n','m']
  ];
  var SYMBOLS = [
    ['1','2','3','4','5','6','7','8','9','0'],
    ['@','#','$','_','&','-','+','(',')','/'],
    ['*','"','\'',':',';','!','?','='],
    [',','.','%','~','|','\\','<','>']
  ];
  var PAD = [['1','2','3'], ['4','5','6'], ['7','8','9'], ['.','0','back']];

  // ── DOM build ───────────────────────────────────────────────────────
  var root = null;        // .osk container
  var target = null;      // currently-focused field
  // FLAG-079: three-state shift for the letters layer.
  //   'off'   — lowercase.
  //   'shift' — one-shot: next letter uppercase, then auto-revert to 'off'.
  //   'caps'  — latched caps-lock: all letters uppercase until tapped off.
  // Tapping ⇧ cycles off → shift → caps → off.
  var shiftState = 'off'; // 'off' | 'shift' | 'caps'
  var mode = 'letters';   // 'letters' | 'symbols' | 'pad'

  // Next shift state in the off → shift → caps → off cycle.
  function nextShiftState(s) {
    return s === 'off' ? 'shift' : (s === 'shift' ? 'caps' : 'off');
  }
  // Letters are uppercase in both the one-shot and latched states.
  function shiftUpper() { return shiftState === 'shift' || shiftState === 'caps'; }

  function makeKey(label, cls, onTap, ariaLabel) {
    var b = document.createElement('button');
    b.type = 'button';
    b.tabIndex = -1;
    b.className = 'osk-key touch-target-pos' + (cls ? ' ' + cls : '');
    b.textContent = label;
    if (ariaLabel) b.setAttribute('aria-label', ariaLabel);
    // Activate on pointerdown, NOT click. The OSK keeps focus on the field by
    // calling preventDefault() on pointerdown (see ensureRoot + below) — but on
    // a touchscreen that preventDefault SUPPRESSES the synthesized click, so a
    // click-bound key never fired and digits didn't type (the cash-modal bug:
    // taps left "Cash Received" empty, then mis-taps closed the modal / crashed
    // the kiosk compositor to a white screen). pointerdown fires for touch,
    // mouse and pen alike; preventDefault here both retains focus and cancels
    // the redundant compat click, so onTap runs exactly once per tap. The click
    // fallback covers environments without Pointer Events (e.g. jsdom).
    if (window.PointerEvent) {
      b.addEventListener('pointerdown', function (e) {
        e.preventDefault();
        onTap();
      });
    } else {
      b.addEventListener('click', function (e) {
        e.preventDefault();
        onTap();
      });
    }
    return b;
  }

  function ensureRoot() {
    if (root) return root;
    root = document.createElement('div');
    root.className = 'osk';
    root.setAttribute('role', 'group');
    root.setAttribute('aria-label', 'On-screen keyboard');
    root.hidden = true;
    // Keep focus on the field: prevent the field from blurring on tap.
    root.addEventListener('pointerdown', function (e) { e.preventDefault(); });
    root.addEventListener('mousedown', function (e) { e.preventDefault(); });
    document.body.appendChild(root);
    return root;
  }

  function render() {
    ensureRoot();
    root.innerHTML = '';
    root.dataset.mode = mode;

    if (mode === 'pad') {
      var grid = document.createElement('div');
      grid.className = 'osk-pad';
      PAD.forEach(function (row) {
        row.forEach(function (k) {
          if (k === 'back') {
            grid.appendChild(makeKey('⌫', 'osk-key--accent', function () { backspace(target); }, 'Backspace'));
          } else if (k === '.') {
            var dot = makeKey('.', '', function () { insertText(target, '.'); }, 'Decimal point');
            if (target && !allowsDecimal(target)) { dot.disabled = true; dot.classList.add('osk-key--muted'); }
            grid.appendChild(dot);
          } else {
            grid.appendChild(makeKey(k, '', (function (ch) { return function () { insertText(target, ch); }; })(k)));
          }
        });
      });
      root.appendChild(grid);

      var padBar = document.createElement('div');
      padBar.className = 'osk-row osk-bar';
      padBar.appendChild(makeKey('Done', 'osk-key--wide osk-key--accent', function () { if (target) target.blur(); hide(); }, 'Hide keyboard'));
      root.appendChild(padBar);
      return;
    }

    var rows = mode === 'symbols' ? SYMBOLS : LETTERS;
    rows.forEach(function (chars, idx) {
      var row = document.createElement('div');
      row.className = 'osk-row';

      // Shift sits at the start of the last character row in letters mode.
      if (mode === 'letters' && idx === rows.length - 1) {
        // Active for both shift and caps; caps gets an extra class so the latch
        // reads distinctly from the one-shot shift.
        var modCls = 'osk-key--mod';
        if (shiftState === 'shift') modCls += ' osk-key--active';
        else if (shiftState === 'caps') modCls += ' osk-key--active osk-key--caps';
        var sk = makeKey('⇧', modCls, function () {
          shiftState = nextShiftState(shiftState); render();
        }, 'Shift');
        row.appendChild(sk);
      }

      chars.forEach(function (ch) {
        var label = (mode === 'letters' && shiftUpper()) ? ch.toUpperCase() : ch;
        row.appendChild(makeKey(label, '', (function (out) {
          return function () {
            insertText(target, out);
            // One-shot shift reverts after a keypress; caps-lock stays latched.
            if (mode === 'letters' && shiftState === 'shift') { shiftState = 'off'; render(); }
          };
        })(label)));
      });

      // Backspace closes the last character row.
      if (idx === rows.length - 1) {
        row.appendChild(makeKey('⌫', 'osk-key--mod osk-key--accent', function () { backspace(target); }, 'Backspace'));
      }
      root.appendChild(row);
    });

    // Bottom action bar: layer toggle · space · enter · hide
    var bar = document.createElement('div');
    bar.className = 'osk-row osk-bar';
    bar.appendChild(makeKey(mode === 'symbols' ? 'ABC' : '?123', 'osk-key--mod', function () {
      mode = mode === 'symbols' ? 'letters' : 'symbols'; shiftState = 'off'; render();
    }, 'Toggle letters and symbols'));
    bar.appendChild(makeKey('space', 'osk-key--space', function () { insertText(target, ' '); }, 'Space'));
    bar.appendChild(makeKey('return', 'osk-key--accent osk-key--wide', function () { submitOrAdvance(target); }, 'Enter'));
    bar.appendChild(makeKey('⌄', 'osk-key--mod', function () { if (target) target.blur(); hide(); }, 'Hide keyboard'));
    root.appendChild(bar);
  }

  // ── Show / hide ─────────────────────────────────────────────────────
  function show(el) {
    // BUG-010: idempotent for the already-active field. A redundant focusin on
    // the field that's already the target (fired by modal focus management,
    // scrollIntoView, or a stray re-render) must NOT wipe the numeric shadow
    // buffer or re-init — doing so loses in-progress input (type=number
    // sanitizes a transient "12." to "" on assignment, so a buffer reset mid
    // entry clears the field) and steals focus, forcing a re-tap per digit.
    if (el === target && root && !root.hidden) return;
    target = el;
    delete el._oskBuf;   // re-sync numeric shadow buffer from the field's value
    mode = wantsNumeric(el) ? 'pad' : 'letters';
    shiftState = 'off';
    render();
    root.hidden = false;
    document.body.classList.add('osk-open');
    // Don't let the keyboard cover the field.
    setTimeout(function () {
      // Instant (not 'smooth') scroll: an animated scroll of a position:fixed
      // modal on the low-end kiosk panel churns the compositor and was a path to
      // the white-screen crash. A jump is fine here.
      try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    }, 0);
  }

  function hide() {
    if (!root || root.hidden) return;
    root.hidden = true;
    target = null;
    document.body.classList.remove('osk-open');
  }

  // Keep the numeric pad up as long as the field's MODAL is still open — the
  // pad is the only way to type digits on a touch kiosk and it kept vanishing
  // mid cash-entry. The trigger: on a real touchscreen a tap on a quick-cash
  // chip / Exact / empty modal chrome often does NOT move focus to a button —
  // focus falls to <body> — and the old handlers hid the pad the instant
  // activeElement was <body> or any non-field. So gate on "is my target field
  // still in an OPEN [data-modal]" rather than on where focus landed. OSK keys
  // type into `target` (not document.activeElement), so typing keeps working
  // while focus sits on a button or <body>.
  //
  // Scoped to numeric-pad mode, so ordinary text-entry modals (e.g. the
  // inventory editor) still dismiss on an outside tap, keeping their Save
  // button reachable. A TEXT field can opt back INTO this persistence with
  // [data-osk-sticky] — used for the GCash/Maya reference number, which the
  // cashier reads off the customer's phone and types over several taps; on the
  // touch kiosk a stray tap dropped focus to <body> and the QWERTY vanished
  // mid-entry (mode !== 'pad', so the body-fallback below never fired), forcing
  // a re-tap of the field for every interruption. Sticky text fields sit in a
  // payment modal whose Confirm stays reachable above the keyboard, so keeping
  // the board up costs nothing. When the modal closes (Complete/Cancel →
  // display:none) the check goes false and the keyboard hides — no lingering.
  function targetModalOpen() {
    if (!target || !target.closest) return false;
    if (mode !== 'pad' && !(target.matches && target.matches('[data-osk-sticky]'))) return false;
    var modal = target.closest('[data-modal]');
    if (!modal) return false;
    var s = window.getComputedStyle(modal);
    return s.display !== 'none' && s.visibility !== 'hidden';
  }

  // Should the pad stay up now that focus moved to `a`?  Keep it while the
  // field's modal is still open, UNLESS focus landed on a real control OUTSIDE
  // that modal (then the user genuinely left). `a` is null/<body> when a touch
  // tap didn't focus anything — the common kiosk case for a button/chip tap —
  // and that must keep the pad, not hide it.
  function shouldStayOpen(a) {
    if (!targetModalOpen()) return false;
    if (!a || a === document.body) return true;
    var modal = target.closest('[data-modal]');
    return !!(modal && modal.contains(a));
  }

  // ── Wiring ──────────────────────────────────────────────────────────
  document.addEventListener('focusin', function (e) {
    var el = e.target;
    if (isEligible(el)) {
      show(el);
    } else if (root && !root.hidden && !root.contains(el) && !shouldStayOpen(el)) {
      hide();
    }
  });

  document.addEventListener('focusout', function () {
    // Defer: focus may be moving to another field or (suppressed) to a key.
    setTimeout(function () {
      var a = document.activeElement;
      if (root && a && root.contains(a)) return;  // keys aren't focusable; guard anyway
      if (isEligible(a)) return;                  // moving to another field; focusin retargets
      if (shouldStayOpen(a)) return;              // pad stays while the field's modal is open
      hide();
    }, 0);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && root && !root.hidden) hide();
  });
})();
