// ISSUE-079 — currency formatter (mirrors canteen/utils/currency.py).
// Symbol resolved from BusinessProfile.currency (biz_profile in localStorage).
// Unknown codes fall back to ISO prefix, e.g. "SGD 1,234.50". JPY: 0 decimals.
(function () {
  const SYMBOLS = { PHP: '₱', USD: '$', EUR: '€', GBP: '£', JPY: '¥' };

  function activeCurrency() {
    if (window.__currency) return String(window.__currency).toUpperCase();
    try {
      const bp = JSON.parse(localStorage.getItem('biz_profile') || '{}');
      return String(bp.currency || 'PHP').toUpperCase();
    } catch (e) {
      return 'PHP';
    }
  }

  window.currencySymbol = function (code) {
    return SYMBOLS[(code || activeCurrency()).toUpperCase()] || '';
  };

  window.formatCurrency = function (amount, code) {
    const c = (code || activeCurrency()).toUpperCase();
    const decimals = c === 'JPY' ? 0 : 2;
    const n = Number(amount);
    const value = Number.isFinite(n) ? n : 0;
    const sign = value < 0 ? '-' : '';
    const formatted = Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    const sym = SYMBOLS[c];
    return sym ? `${sign}${sym}${formatted}` : `${sign}${c} ${formatted}`;
  };
})();
