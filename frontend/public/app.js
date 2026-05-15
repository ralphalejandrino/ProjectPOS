// ============================================
// GLOBAL STATE
// ============================================

window.cart = {
    items: [],
    total: 0,
    discountAmount: 0,
    discountType: '',
    discountIdNumber: '',
    discountLabel: '',
    selectedDiscountType: null,
    pendingDiscountType: null,
};

let allProducts = [];
let allCategories = [];

// ============================================
// CART TOTAL — single source of truth (QA-S2-002)
// ============================================

function getCartTotal() {
    const subtotal = window.cart.items.reduce((sum, item) => {
        const price = parseFloat(item.price) || 0;
        const quantity = parseInt(item.quantity) || 0;
        return sum + (price * quantity);
    }, 0);
    return Math.max(0, subtotal - (window.cart.discountAmount || 0));
}
window.getCartTotal = getCartTotal;

// Reset cart + ALL discount state (QA-S2-001) — used by every clear path
// so the display and payment modal can never diverge.
function clearCartState() {
    window.cart.items = [];
    window.cart.total = 0;
    window.cart.discountAmount = 0;
    window.cart.discountType = null;
    window.cart.discountIdNumber = '';
    window.cart.discountLabel = null;
    window.cart.selectedDiscountType = null;
    window.cart.pendingDiscountType = null;
}
window.clearCartState = clearCartState;

// ============================================
// API BASE URL
// ============================================

// API_BASE is defined in config.js (loaded before this script)

// ============================================
// INITIALIZATION
// ============================================

document.addEventListener('DOMContentLoaded', async () => {
    await loadCategories();
    await loadProducts();
    attachEventListeners();
    // Role-gate: manager/admin-only elements
    const role = getUserRole();
    if (role === 'manager' || role === 'admin') {
        const noSaleBtn = document.getElementById('btn-no-sale');
        if (noSaleBtn) noSaleBtn.classList.remove('hidden');
        const zReportLink = document.getElementById('nav-zreport');
        if (zReportLink) { zReportLink.classList.remove('hidden'); zReportLink.classList.add('block'); }
    }
    // Non-blocking shift check — fires after POS is ready
    checkOpenShift();
});

function noSale() {
    const modal = document.getElementById('no-sale-modal');
    if (modal) {
        modal.classList.remove('hidden');
    } else {
        // Fallback for pages without the modal
        authenticatedFetch(`${API_BASE}/transactions/kick_drawer/`, { method: 'POST' })
            .catch(() => {});
    }
}

// ============================================
// LOAD CATEGORIES
// ============================================

async function loadCategories() {
    try {
        const response = await authenticatedFetch(`${API_BASE}/categories/`);
        const data = await response.json();
        allCategories = data.results !== undefined ? data.results : data;
        displayCategories();
    } catch (error) {
    }
}

function displayCategories() {
    const container = document.getElementById('category-buttons');
    
    if (!container) {
        return;
    }
    
    container.innerHTML = '';
    
    // Add "All" button
    const allBtn = document.createElement('button');
    allBtn.className = 'w-full px-4 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition text-left';
    allBtn.textContent = '📦 All Products';
    allBtn.onclick = () => filterByCategory(null);
    container.appendChild(allBtn);

    // Add category buttons
    allCategories.forEach(cat => {
        const btn = document.createElement('button');
        btn.className = 'w-full px-4 py-3 bg-gray-100 text-gray-700 rounded-lg font-medium hover:bg-gray-200 transition text-left';
        btn.textContent = `${cat.emoji || '🏷️'} ${cat.name}`;
        btn.dataset.categoryId = cat.id; // use data attribute for reliable matching
        btn.onclick = () => filterByCategory(cat.id);
        container.appendChild(btn);
    });
}

// ============================================
// LOAD PRODUCTS
// ============================================

async function loadProducts() {
    try {
        const response = await authenticatedFetch(`${API_BASE}/items/`);
        const data = await response.json();
        allProducts = data.results !== undefined ? data.results : data;
        displayProducts(allProducts);
    } catch (error) {
        const grid = document.getElementById('products-grid');
        if (grid) {
            grid.innerHTML = '<p class="col-span-full text-red-500 text-center p-4">Failed to load menu items. Please refresh the page.</p>';
        }
    }
}

function displayProducts(products) {
    const grid = document.getElementById('products-grid');

    if (!grid) {
        return;
    }

    grid.innerHTML = '';

    if (products.length === 0) {
        grid.innerHTML = '<p class="col-span-full text-center text-gray-500 py-8">No products found</p>';
        return;
    }

    const _biz = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const trackInventory = _biz.track_inventory !== false;

    products.forEach(product => {
        const outOfStock = trackInventory && product.stock <= 0;
        const card = document.createElement('div');
        card.className = `product-card bg-white rounded-lg shadow-md cursor-pointer hover:shadow-lg overflow-hidden relative${outOfStock ? ' opacity-60' : ''}`;
        card.dataset.productId = product.id; // Added for robust stock sync
        card.onclick = () => { if (outOfStock) return; addToCart(product); };

          // Stock status
        const stockClass = product.stock > 10 ? 'text-green-600' :
                          product.stock > 0 ? 'text-orange-600' : 'text-red-600';
        const stockText = trackInventory
            ? (product.stock > 0 ? `${product.stock} in stock` : 'Out of stock')
            : '';

        card.innerHTML = `
            <div class="relative w-full h-40">
                ${product.photo
                    ? `<img src="${escapeHtml(product.photo)}" alt="${escapeHtml(product.name)}" class="w-full h-full object-cover" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                       <div class="w-full h-full bg-gray-100 items-center justify-center text-5xl" style="display:none">📦</div>`
                    : `<div class="w-full h-full bg-gray-100 flex items-center justify-center text-5xl">📦</div>`
                }
                <div class="absolute inset-0 bg-gradient-to-t from-black/70 to-transparent"></div>
                <div class="absolute bottom-0 left-0 right-0 px-2 pb-2 flex justify-between items-end">
                    <span class="text-white font-bold text-base leading-tight drop-shadow">${escapeHtml(product.name)}</span>
                    <div class="text-right">
                        <div class="text-white font-bold text-base drop-shadow">₱${parseFloat(product.price).toFixed(2)}</div>
                        ${stockText ? `<div class="text-xs ${stockClass} font-medium drop-shadow">${stockText}</div>` : ''}
                    </div>
                </div>
            </div>
        `;
        if (outOfStock) {
            const overlay = document.createElement('div');
            overlay.className = 'out-of-stock-overlay absolute inset-0 bg-gray-900 bg-opacity-60 flex items-center justify-center rounded-lg z-10 pointer-events-none';
            overlay.innerHTML = '<span class="bg-gray-800 text-white text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wide">Out of Stock</span>';
            card.appendChild(overlay);
        }

        grid.appendChild(card);
    });
}

// ============================================
// FILTER & SEARCH
// ============================================

function filterByCategory(categoryId) {
    if (categoryId === null) {
        displayProducts(allProducts);
    } else {
        const filtered = allProducts.filter(p => p.category === categoryId);
        displayProducts(filtered);
    }
    
    const buttons = document.querySelectorAll('#category-buttons button');
    buttons.forEach(btn => {
        const isAllBtn = !btn.dataset.categoryId;
        const isActive = (categoryId === null && isAllBtn) || 
                         (categoryId !== null && btn.dataset.categoryId === categoryId);
        
        btn.className = isActive 
            ? 'w-full px-4 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 transition text-left' 
            : 'w-full px-4 py-3 bg-gray-100 text-gray-700 rounded-lg font-medium hover:bg-gray-200 transition text-left';
    });
}

function searchProducts() {
    const query = document.getElementById('search-input').value.toLowerCase();
    const filtered = allProducts.filter(p => 
        p.name.toLowerCase().includes(query) || 
        (p.category_name && p.category_name.toLowerCase().includes(query))
    );
    displayProducts(filtered);
}

// ============================================
// CART MANAGEMENT
// ============================================

// ── Variant picker state (Step 8) ────────────────────────────────────────────
let _variantPickerItem = null;
let _variantSelections = {};

function addToCart(product) {
    const _biz2 = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const trackInventory = _biz2.track_inventory !== false;

    if (trackInventory && product.stock === 0) {
        if (window.showCustomError) {
            window.showCustomError('Out of Stock', `${product.name} is currently out of stock`);
        } else {
            console.error('Out of stock:', product.name);
            (function() {
                const el = document.createElement('div');
                el.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;z-index:99999;font-weight:600;';
                el.textContent = product.name + ' is out of stock!';
                document.body.appendChild(el);
                setTimeout(() => el.remove(), 3000);
            })();
        }
        return;
    }

    const effectiveGroups = product.effective_variant_groups || [];
    const activeGroups = effectiveGroups.filter(eg => eg.group && eg.group.options && eg.group.options.some(o => o.is_active));
    if (activeGroups.length > 0) {
        showVariantPicker(product);
        return;
    }
    addProductToCart(product, [], parseFloat(product.price));
}

function addProductToCart(item, variantSelections, finalPrice) {
    const _biz2 = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const trackInventory = _biz2.track_inventory !== false;

    variantSelections = variantSelections || [];
    const variantKey = variantSelections.map(s => s.option_id).sort().join('_');
    const cartKey = variantSelections.length > 0 ? `${item.id}__${variantKey}` : String(item.id);
    const variantLabel = variantSelections.map(s => s.option_name).join(', ');
    const displayName = variantLabel ? `${item.name} (${variantLabel})` : item.name;
    const price = finalPrice !== undefined ? finalPrice : parseFloat(item.price);

    const existingItem = window.cart.items.find(i => i.cartKey === cartKey);

    if (existingItem) {
        if (!trackInventory || existingItem.quantity < item.stock + existingItem.quantity) {
            existingItem.quantity++;
            updateProductStock(item.id, -1);
        } else {
            if (window.showCustomError) {
                window.showCustomError('Stock Limit', 'Cannot add more than available stock!');
            } else {
                console.error('Stock limit reached for:', item.name);
                (function() {
                    const el = document.createElement('div');
                    el.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;z-index:99999;font-weight:600;';
                    el.textContent = 'Cannot add more than available stock!';
                    document.body.appendChild(el);
                    setTimeout(() => el.remove(), 3000);
                })();
            }
            return;
        }
    } else {
        window.cart.items.push({
            cartKey,
            id: item.id,
            name: displayName,
            baseName: item.name,
            price,
            quantity: 1,
            variant_selections: variantSelections,
            emoji: item.emoji || '🍽️',
        });
        updateProductStock(item.id, -1);
    }
    updateCart();
}

function showVariantPicker(item) {
    _variantPickerItem = item;
    _variantSelections = {};
    document.getElementById('variant-modal-title').textContent = escapeHtml(item.name);
    const groups = document.getElementById('variant-modal-groups');
    groups.innerHTML = '';
    const effectiveGroups = item.effective_variant_groups || [];
    if (effectiveGroups.length === 0) {
        addProductToCart(item, [], parseFloat(item.price));
        return;
    }
    effectiveGroups.forEach(eg => {
        const g = eg.group;
        const required = eg.is_required;
        const section = document.createElement('div');
        section.className = 'border border-gray-200 dark:border-gray-700 rounded-lg p-3';
        section.innerHTML = `<div class="flex items-center gap-2 mb-2">
            <span class="font-medium text-sm text-gray-700 dark:text-gray-300">${escapeHtml(g.name)}</span>
            ${required ? '<span class="text-xs text-red-500">Required</span>' : ''}
            <span class="text-xs text-gray-400">${g.selection_type === 'multi' ? 'Choose multiple' : 'Choose one'}</span>
        </div>`;
        const opts = document.createElement('div');
        opts.className = 'flex flex-col gap-1';
        (g.options || []).filter(o => o.is_active).forEach(opt => {
            const inputType = g.selection_type === 'multi' ? 'checkbox' : 'radio';
            const inputName = `vg_${g.id}`;
            const pm = parseFloat(opt.price_modifier);
            const priceLabel = pm > 0 ? `+₱${pm.toFixed(2)}` : pm < 0 ? `-₱${Math.abs(pm).toFixed(2)}` : '';
            const row = document.createElement('label');
            row.className = 'flex items-center gap-2 cursor-pointer text-sm text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200';
            row.innerHTML = `<input type="${inputType}" name="${inputName}" value="${opt.id}" data-group="${g.id}" data-price="${opt.price_modifier}" class="variant-input"> ${escapeHtml(opt.name)}${priceLabel ? ' <span class="text-indigo-500">' + escapeHtml(priceLabel) + '</span>' : ''}`;
            opts.appendChild(row);
        });
        section.appendChild(opts);
        groups.appendChild(section);
    });
    groups.querySelectorAll('.variant-input').forEach(input => {
        input.addEventListener('change', updateVariantPrice);
    });
    updateVariantPrice();
    document.getElementById('variant-modal').classList.remove('hidden');
}

function updateVariantPrice() {
    if (!_variantPickerItem) return;
    let total = parseFloat(_variantPickerItem.price);
    document.querySelectorAll('.variant-input:checked').forEach(input => {
        total += parseFloat(input.dataset.price || 0);
    });
    document.getElementById('variant-modal-price').textContent = `₱${total.toFixed(2)}`;
}

function closeVariantModal() {
    document.getElementById('variant-modal').classList.add('hidden');
    _variantPickerItem = null;
    _variantSelections = {};
}

function confirmVariantSelection() {
    const item = _variantPickerItem;
    if (!item) return;
    const effectiveGroups = item.effective_variant_groups || [];
    const selections = [];
    let valid = true;
    effectiveGroups.forEach(eg => {
        if (!valid) return;
        const g = eg.group;
        const required = eg.is_required;
        const checked = document.querySelectorAll(`.variant-input[data-group="${g.id}"]:checked`);
        if (required && checked.length === 0) {
            if (window.showCustomError) {
                window.showCustomError('Selection Required', `Please select a "${escapeHtml(g.name)}" option.`);
            }
            valid = false;
            return;
        }
        checked.forEach(input => {
            const opt = (g.options || []).find(o => String(o.id) === String(input.value));
            if (opt) selections.push({
                group_id: g.id,
                option_id: opt.id,
                group_name: g.name,
                option_name: opt.name,
                price_modifier: opt.price_modifier
            });
        });
    });
    if (!valid) return;
    const finalPrice = parseFloat(item.price) + selections.reduce((sum, s) => sum + parseFloat(s.price_modifier), 0);
    closeVariantModal();
    addProductToCart(item, selections, finalPrice);
}

function removeFromCart(cartKey) {
    const removedItem = window.cart.items.find(item => item.cartKey === cartKey);
    if (removedItem) {
        updateProductStock(removedItem.id, removedItem.quantity); // restore all qty
    }
    window.cart.items = window.cart.items.filter(item => item.cartKey !== cartKey);
    updateCart();
}

function updateQuantity(cartKey, change) {
    const item = window.cart.items.find(i => i.cartKey === cartKey);
    if (item) {
        if (item.quantity + change <= 0) {
            removeFromCart(cartKey); // removeFromCart handles stock restore using current item.quantity
        } else {
            item.quantity += change;
            updateProductStock(item.id, -change); // +1 qty = -1 stock, vice versa
            updateCart();
        }
    }
}

function updateCart() {
    const cartItemsContainer = document.getElementById('cart-items');
    const cartTotal = document.getElementById('cart-total');
    const cartCount = document.getElementById('cart-count');

    // Total (incl. discount) — single source of truth shared with payment modal
    window.cart.total = getCartTotal();

    // Update count
    const totalItems = window.cart.items.reduce((sum, item) => sum + item.quantity, 0);
    cartCount.textContent = totalItems;

    // Update total display
    cartTotal.textContent = `₱${parseFloat(window.cart.total || 0).toFixed(2)}`;
    
    // Update items
    if (window.cart.items.length === 0) {
        cartItemsContainer.innerHTML = '<p class="text-gray-400 text-center py-8">Cart is empty</p>';
    } else {
        cartItemsContainer.innerHTML = window.cart.items.map(item => {
            const variantSubtitle = item.variant_selections && item.variant_selections.length > 0
                ? `<div class="text-xs text-gray-400">${escapeHtml(item.variant_selections.map(s => s.option_name).join(', '))}</div>`
                : '';
            return `
            <div class="cart-item bg-gray-50 p-3 rounded-lg">
                <div class="flex justify-between items-start mb-2">
                    <div class="flex-1">
                        <div class="font-bold text-gray-800">${escapeHtml(item.emoji)} ${escapeHtml(item.baseName || item.name)}</div>
                        ${variantSubtitle}
                        <div class="text-sm text-gray-600">₱${parseFloat(item.price || 0).toFixed(2)} each</div>
                    </div>
                    <button onclick="removeFromCart('${item.cartKey}')" class="text-red-500 hover:text-red-700 font-bold">
                        ✕
                    </button>
                </div>
                <div class="flex justify-between items-center">
                    <div class="flex items-center space-x-2">
                        <button onclick="updateQuantity('${item.cartKey}', -1)" class="bg-gray-300 hover:bg-gray-400 w-8 h-8 rounded font-bold">-</button>
                        <span class="w-12 text-center font-bold">${item.quantity}</span>
                        <button onclick="updateQuantity('${item.cartKey}', 1)" class="bg-blue-600 hover:bg-blue-700 text-white w-8 h-8 rounded font-bold">+</button>
                    </div>
                    <div class="font-bold text-blue-600">₱${parseFloat((item.price * item.quantity) || 0).toFixed(2)}</div>
                </div>
            </div>
        `}).join('');
    }
}

// ============================================
// EVENT LISTENERS
// ============================================

function attachEventListeners() {
    // Clear cart button
    const clearCartBtn = document.getElementById('clear-cart-btn');
    if (clearCartBtn) {
        clearCartBtn.addEventListener('click', () => {
            if (window.cart.items.length === 0) {
                if (window.showCustomError) {
                    window.showCustomError('Cart Empty', 'Cart is already empty');
                }
                return;
            }
            
            if (window.showCustomConfirm) {
                window.showCustomConfirm(
                    'Clear Cart?',
                    'Are you sure you want to remove all items from the cart?',
                    () => {
                        window.cart.items.forEach(i => updateProductStock(i.id, i.quantity));
                        clearCartState();
                        updateCart();
                    }
                );
            } else {
                // Inline confirmation — no native confirm() dialog
                (function() {
                    const overlay = document.createElement('div');
                    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:99999;';
                    const box = document.createElement('div');
                    box.style.cssText = 'background:#fff;border-radius:12px;padding:24px;max-width:320px;width:90%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,0.2);';
                    box.innerHTML = '<p style="font-weight:700;font-size:1.1rem;margin-bottom:8px;">Clear Cart?</p><p style="color:#555;margin-bottom:20px;">Are you sure you want to remove all items from the cart?</p>';
                    const btnRow = document.createElement('div');
                    btnRow.style.cssText = 'display:flex;gap:12px;justify-content:center;';
                    const cancelBtn = document.createElement('button');
                    cancelBtn.textContent = 'No';
                    cancelBtn.style.cssText = 'flex:1;padding:10px;border-radius:8px;border:1px solid #ccc;background:#f3f4f6;font-weight:600;cursor:pointer;';
                    const confirmBtn = document.createElement('button');
                    confirmBtn.textContent = 'Yes, Clear';
                    confirmBtn.style.cssText = 'flex:1;padding:10px;border-radius:8px;border:none;background:#dc2626;color:#fff;font-weight:600;cursor:pointer;';
                    cancelBtn.onclick = () => document.body.removeChild(overlay);
                    confirmBtn.onclick = () => {
                        document.body.removeChild(overlay);
                        // i.id is the item UUID (unchanged — stock tracking uses item.id not cartKey)
                        window.cart.items.forEach(i => updateProductStock(i.id, i.quantity));
                        window.cart.items = [];
                        window.cart.total = 0;
                        updateCart();
                    };
                    btnRow.appendChild(cancelBtn);
                    btnRow.appendChild(confirmBtn);
                    box.appendChild(btnRow);
                    overlay.appendChild(box);
                    document.body.appendChild(overlay);
                })();
            }
        });
    }
}

// Make functions globally available
window.addToCart = addToCart;
window.addProductToCart = addProductToCart;
window.showVariantPicker = showVariantPicker;
window.closeVariantModal = closeVariantModal;
window.confirmVariantSelection = confirmVariantSelection;
window.updateVariantPrice = updateVariantPrice;
window.removeFromCart = removeFromCart;
window.updateQuantity = updateQuantity;
window.updateCart = updateCart;
window.filterByCategory = filterByCategory;
window.searchProducts = searchProducts;

// ============================================
// PAYMENT MODAL DISCOUNT FUNCTIONS
// ============================================

function initModalDiscount(prefix) {
    const profile = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const discountBtn = document.getElementById(prefix + '-discount-btn');
    const scBtn = document.getElementById(prefix + '-btn-sc');
    const pwdBtn = document.getElementById(prefix + '-btn-pwd');
    const promoBtn = document.getElementById(prefix + '-btn-promo');

    const anyEnabled = profile.sc_discount_enabled || profile.pwd_discount_enabled || profile.promo_discount_enabled;
    if (discountBtn && anyEnabled) discountBtn.classList.remove('hidden');
    else if (discountBtn) discountBtn.classList.add('hidden');
    if (scBtn) scBtn.classList.toggle('hidden', !profile.sc_discount_enabled);
    if (pwdBtn) pwdBtn.classList.toggle('hidden', !profile.pwd_discount_enabled);
    if (promoBtn) promoBtn.classList.toggle('hidden', !profile.promo_discount_enabled);

    clearModalDiscount(prefix);
}
window.initModalDiscount = initModalDiscount;

function toggleDiscountSection(prefix) {
    const section = document.getElementById(prefix + '-discount-section');
    if (section) section.classList.toggle('hidden');
}
window.toggleDiscountSection = toggleDiscountSection;

function selectDiscountType(prefix, type) {
    const profile = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();

    ['sc', 'pwd', 'promo'].forEach(t => {
        const btn = document.getElementById(prefix + '-btn-' + t);
        if (btn) {
            btn.classList.toggle('border-blue-500', t === type);
            btn.classList.toggle('text-blue-700', t === type);
            btn.classList.toggle('bg-blue-50', t === type);
            btn.classList.toggle('border-gray-300', t !== type);
            btn.classList.toggle('text-gray-600', t !== type);
        }
    });

    const idInput = document.getElementById(prefix + '-discount-id');
    const promoInput = document.getElementById(prefix + '-promo-input');
    if (idInput) idInput.classList.toggle('hidden', type === 'promo');
    if (promoInput) promoInput.classList.toggle('hidden', type !== 'promo');

    window.cart.selectedDiscountType = type;

    if (type === 'sc' || type === 'pwd') {
        window.cart.pendingDiscountType = type;
        window.cart.pendingDiscountRate = type === 'sc'
            ? (parseFloat(profile.sc_discount_rate) || 20)
            : (parseFloat(profile.pwd_discount_rate) || 20);
        applyModalDiscount(prefix);
    }
}
window.selectDiscountType = selectDiscountType;

function applyModalDiscount(prefix) {
    const profile = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const type = window.cart.selectedDiscountType || window.cart.pendingDiscountType;
    if (!type) return;

    const subtotal = window.cart.items.reduce((sum, i) => sum + (parseFloat(i.price) * i.quantity), 0);
    const vatRate = profile.vat_enabled ? ((profile.vat_rate !== undefined && profile.vat_rate !== null && !isNaN(parseFloat(profile.vat_rate))) ? parseFloat(profile.vat_rate) : 12) / 100 : 0;

    let discountAmount = 0;
    let discountLabel = '';

    if (type === 'sc') {
        const rate = parseFloat(profile.sc_discount_rate) || 20;
        const netSubtotal = subtotal / (1 + vatRate);
        discountAmount = netSubtotal * (rate / 100);
        discountLabel = 'SC Discount (' + rate + '%)';
    } else if (type === 'pwd') {
        const rate = parseFloat(profile.pwd_discount_rate) || 20;
        const netSubtotal = subtotal / (1 + vatRate);
        discountAmount = netSubtotal * (rate / 100);
        discountLabel = 'PWD Discount (' + rate + '%)';
    } else if (type === 'promo') {
        const pct = parseFloat(document.getElementById(prefix + '-promo-pct').value) || 0;
        discountAmount = subtotal * (pct / 100);
        discountLabel = 'Promo Discount (' + pct + '%)';
    }

    const discountId = document.getElementById(prefix + '-discount-id')?.value || '';

    window.cart.discountAmount = discountAmount;
    window.cart.discountType = type;
    window.cart.discountIdNumber = discountId;
    window.cart.discountLabel = discountLabel;

    // Recompute cart.total so calculateChange() stays accurate
    updateCart();

    const newTotal = Math.max(0, subtotal - discountAmount);

    // Update the modal total display
    if (prefix === 'cash') {
        const cashTotalEl = document.getElementById('cash-total');
        if (cashTotalEl) cashTotalEl.textContent = '₱' + newTotal.toFixed(2);
    } else {
        const spanEl = document.getElementById(prefix + '-total');
        if (spanEl) spanEl.textContent = newTotal.toFixed(2);
    }

    const display = document.getElementById(prefix + '-discount-display');
    if (display) {
        display.textContent = discountLabel + ': -₱' + discountAmount.toFixed(2);
        display.classList.remove('hidden');
    }

    const clearBtn = document.getElementById(prefix + '-clear-discount');
    if (clearBtn) clearBtn.classList.remove('hidden');
}
window.applyModalDiscount = applyModalDiscount;

function clearModalDiscount(prefix) {
    window.cart.discountAmount = 0;
    window.cart.discountType = '';
    window.cart.discountIdNumber = '';
    window.cart.discountLabel = '';
    window.cart.selectedDiscountType = null;
    window.cart.pendingDiscountType = null;

    updateCart();

    const subtotal = window.cart.items.reduce((sum, i) => sum + (parseFloat(i.price) * i.quantity), 0);
    if (prefix === 'cash') {
        const cashTotalEl = document.getElementById('cash-total');
        if (cashTotalEl) cashTotalEl.textContent = '₱' + subtotal.toFixed(2);
    } else {
        const spanEl = document.getElementById(prefix + '-total');
        if (spanEl) spanEl.textContent = subtotal.toFixed(2);
    }

    const display = document.getElementById(prefix + '-discount-display');
    if (display) display.classList.add('hidden');
    const clearBtn = document.getElementById(prefix + '-clear-discount');
    if (clearBtn) clearBtn.classList.add('hidden');
    const idInput = document.getElementById(prefix + '-discount-id');
    if (idInput) { idInput.value = ''; idInput.classList.add('hidden'); }
    const promoInput = document.getElementById(prefix + '-promo-input');
    if (promoInput) promoInput.classList.add('hidden');
    const section = document.getElementById(prefix + '-discount-section');
    if (section) section.classList.add('hidden');

    ['sc', 'pwd', 'promo'].forEach(t => {
        const btn = document.getElementById(prefix + '-btn-' + t);
        if (btn) {
            btn.classList.remove('border-blue-500', 'text-blue-700', 'bg-blue-50');
            btn.classList.add('border-gray-300', 'text-gray-600');
        }
    });
}
window.clearModalDiscount = clearModalDiscount;

// ============================================
// STOCK DISPLAY SYNC (Bug Fix #3)
// ============================================
function updateProductStock(productId, delta) {
    const product = allProducts.find(p => p.id === productId);
    if (!product) return;

    product.stock = Math.max(0, product.stock + delta);

    const _biz3 = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    if (_biz3.track_inventory === false) return; // no visual updates when tracking disabled

    const grid = document.getElementById('products-grid');
    if (!grid) return;

    grid.querySelectorAll('.product-card').forEach(card => {
        if (card.dataset.productId !== product.id) return;

        // Update out-of-stock overlay
        const existingOverlay = card.querySelector('.out-of-stock-overlay');
        if (product.stock === 0) {
            card.classList.add('opacity-60');
            card.onclick = () => {};
            if (!existingOverlay) {
                const overlay = document.createElement('div');
                overlay.className = 'out-of-stock-overlay absolute inset-0 bg-gray-900 bg-opacity-60 flex items-center justify-center rounded-lg z-10 pointer-events-none';
                overlay.innerHTML = '<span class="bg-gray-800 text-white text-xs font-bold px-3 py-1 rounded-full uppercase tracking-wide">Out of Stock</span>';
                card.appendChild(overlay);
            }
        } else {
            card.classList.remove('opacity-60');
            if (existingOverlay) existingOverlay.remove();
            card.onclick = () => addToCart(product);
        }

        const stockEl = card.querySelector('div.text-xs');
        if (!stockEl) return;

        stockEl.className = product.stock > 10
            ? 'text-xs text-green-600 font-medium'
            : product.stock > 0
                ? 'text-xs text-orange-600 font-medium'
                : 'text-xs text-red-600 font-medium';

        stockEl.textContent = product.stock > 0 ? `${product.stock} in stock` : 'Out of stock';
    });
}

// ============================================
// SHIFT PROMPT (bug-024)
// ============================================
async function checkOpenShift() {
    try {
        const response = await authenticatedFetch(`${API_BASE}/shifts/current/`);
        if (response.ok) {
            const shift = await response.json();
            if (!shift) {
                showShiftPrompt();
            }
        }
    } catch (e) {
        // Silently fail — never block the POS if shift check errors
    }
}

function showShiftPrompt() {
    const modal = document.getElementById('shift-modal');
    if (modal) modal.classList.remove('hidden');
}

async function openShift() {
    try {
        const response = await authenticatedFetch(`${API_BASE}/shifts/open/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ opening_cash: 0 })
        });
        const modal = document.getElementById('shift-modal');
        if (modal) modal.classList.add('hidden');
        if (response.ok) {
            const successBanner = document.getElementById('shift-success-banner');
            if (successBanner) {
                successBanner.classList.remove('hidden');
                setTimeout(() => successBanner.classList.add('hidden'), 3500);
            }
            // Refresh header identity after shift opens
            try {
                const tok = localStorage.getItem('access_token');
                if (tok) {
                    const p = JSON.parse(atob(tok.split('.')[1]));
                    const uEl = document.getElementById('userName');
                    const rEl = document.getElementById('userRole');
                    if (uEl) uEl.textContent = p.username || 'User';
                    if (rEl) rEl.textContent = (p.role || 'user').toUpperCase();
                }
            } catch (_) {}
        }
    } catch (e) {
        // Silently fail
    }
}

window.openShift = openShift;
window.closeShiftModal = function() {
    const modal = document.getElementById('shift-modal');
    if (modal) modal.classList.add('hidden');
};
