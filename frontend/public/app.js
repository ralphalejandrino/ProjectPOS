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
        console.error('Failed to load categories:', error);
    }
}

function displayCategories() {
    const container = document.getElementById('category-buttons');
    
    if (!container) {
        console.error('category-buttons element not found!');
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
        console.error('Failed to load products:', error);
        const grid = document.getElementById('products-grid');
        if (grid) {
            grid.innerHTML = '<p class="col-span-full text-red-500 text-center p-4">Failed to load menu items. Please refresh the page.</p>';
        }
    }
}

function displayProducts(products) {
    const grid = document.getElementById('products-grid');

    if (!grid) {
        console.error('products-grid element not found!');
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
                    ? `<img src="${product.photo}" alt="${product.name}" class="w-full h-full object-cover" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                       <div class="w-full h-full bg-gray-100 items-center justify-center text-5xl" style="display:none">📦</div>`
                    : `<div class="w-full h-full bg-gray-100 flex items-center justify-center text-5xl">📦</div>`
                }
                <div class="absolute inset-0 bg-gradient-to-t from-black/70 to-transparent"></div>
                <div class="absolute bottom-0 left-0 right-0 px-2 pb-2 flex justify-between items-end">
                    <span class="text-white font-bold text-base leading-tight drop-shadow">${product.name}</span>
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

function addToCart(product) {
    const _biz2 = (() => { try { return JSON.parse(localStorage.getItem('biz_profile') || '{}'); } catch(e) { return {}; } })();
    const trackInventory = _biz2.track_inventory !== false;

    if (trackInventory && product.stock === 0) {
        if (window.showCustomError) {
            window.showCustomError('Out of Stock', `${product.name} is currently out of stock`);
        } else {
            alert('This item is out of stock!');
        }
        return;
    }

    // Check if item already in cart
    const existingItem = window.cart.items.find(item => item.id === product.id);

    if (existingItem) {
        if (!trackInventory || existingItem.quantity < product.stock + existingItem.quantity) {
            existingItem.quantity++;
            updateProductStock(product.id, -1); // decrement display (qty++)
        } else {
            if (window.showCustomError) {
                window.showCustomError('Stock Limit', 'Cannot add more than available stock!');
            } else {
                alert('Cannot add more than available stock!');
            }
            return;
        }
    } else {
        window.cart.items.push({
            id: product.id,
            name: product.name,
            price: parseFloat(product.price),
            quantity: 1,
            emoji: product.emoji || '🍽️'
        });
        updateProductStock(product.id, -1); // decrement display
    }
    updateCart();
}

function removeFromCart(productId) {
    const removedItem = window.cart.items.find(item => item.id === productId);
    if (removedItem) {
        updateProductStock(productId, removedItem.quantity); // restore all qty
    }
    window.cart.items = window.cart.items.filter(item => item.id !== productId);
    updateCart();
}

function updateQuantity(productId, change) {
    const item = window.cart.items.find(item => item.id === productId);
    if (item) {
        if (item.quantity + change <= 0) {
            removeFromCart(productId); // removeFromCart handles stock restore using current item.quantity
        } else {
            item.quantity += change;
            updateProductStock(productId, -change); // +1 qty = -1 stock, vice versa
            updateCart();
        }
    }
}

function updateCart() {
    const cartItemsContainer = document.getElementById('cart-items');
    const cartTotal = document.getElementById('cart-total');
    const cartCount = document.getElementById('cart-count');

    // Calculate subtotal
    const subtotal = window.cart.items.reduce((sum, item) => {
        const price = parseFloat(item.price) || 0;
        const quantity = parseInt(item.quantity) || 0;
        return sum + (price * quantity);
    }, 0);

    // Apply discount from modal (always active when set)
    const discount = window.cart.discountAmount || 0;
    window.cart.total = Math.max(0, subtotal - discount);

    // Update count
    const totalItems = window.cart.items.reduce((sum, item) => sum + item.quantity, 0);
    cartCount.textContent = totalItems;

    // Update total display
    cartTotal.textContent = `₱${parseFloat(window.cart.total || 0).toFixed(2)}`;
    
    // Update items
    if (window.cart.items.length === 0) {
        cartItemsContainer.innerHTML = '<p class="text-gray-400 text-center py-8">Cart is empty</p>';
    } else {
        cartItemsContainer.innerHTML = window.cart.items.map(item => `
            <div class="cart-item bg-gray-50 p-3 rounded-lg">
                <div class="flex justify-between items-start mb-2">
                    <div class="flex-1">
                        <div class="font-bold text-gray-800">${item.emoji} ${item.name}</div>
                        <div class="text-sm text-gray-600">₱${parseFloat(item.price || 0).toFixed(2)} each</div>
                    </div>
                    <button onclick="removeFromCart('${item.id}')" class="text-red-500 hover:text-red-700 font-bold">
                        ✕
                    </button>
                </div>
                <div class="flex justify-between items-center">
                    <div class="flex items-center space-x-2">
                        <button onclick="updateQuantity('${item.id}', -1)" class="bg-gray-300 hover:bg-gray-400 w-8 h-8 rounded font-bold">-</button>
                        <span class="w-12 text-center font-bold">${item.quantity}</span>
                        <button onclick="updateQuantity('${item.id}', 1)" class="bg-blue-600 hover:bg-blue-700 text-white w-8 h-8 rounded font-bold">+</button>
                    </div>
                    <div class="font-bold text-blue-600">₱${parseFloat((item.price * item.quantity) || 0).toFixed(2)}</div>
                </div>
            </div>
        `).join('');
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
                        window.cart.items = [];
                        window.cart.total = 0;
                        updateCart();
                    }
                );
            } else if (confirm('Clear all items from cart?')) {
                window.cart.items.forEach(i => updateProductStock(i.id, i.quantity));
                window.cart.items = [];
                window.cart.total = 0;
                updateCart();
            }
        });
    }
}

// Make functions globally available
window.addToCart = addToCart;
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
    const vatRate = profile.vat_enabled ? (parseFloat(profile.vat_rate) || 12) / 100 : 0.12;

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
