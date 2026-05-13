// Payment Gateway Integration
const PaymentSystem = {
    config: {
        gcash: { is_active: true, use_mock_mode: true },
        maya: { is_active: true, use_mock_mode: true, enable_terminal: false }
    },

    async init() {
        try {
            await this.loadConfig();
        } catch (error) {
            console.error('PaymentSystem: loadConfig failed —', error.message);
            const msg = 'Payment configuration could not be loaded. Digital payments are disabled until the page reloads successfully.';
            if (window.showCustomError) {
                window.showCustomError('Payment Config Error', msg);
            } else {
                console.warn(msg);
            }
            this.configLoadFailed = true;
        }
        this.attachEventListeners();
    },

    async loadConfig() {
        const response = await authenticatedFetch(`${PAYMENTS_API}/config/`);
        if (!response.ok) {
            throw new Error(`Payment config unavailable (HTTP ${response.status})`);
        }
        const data = await response.json();
        if (data.success) {
            this.config = data.configs;
        } else {
            throw new Error('Payment config load failed: ' + (data.message || 'Unknown error'));
        }
    },

    attachEventListeners() {
        const gcashBtn = document.getElementById('gcash-payment-btn');
        const mayaBtn = document.getElementById('maya-payment-btn');
        const cardBtn = document.getElementById('card-payment-btn');
        if (gcashBtn) {
            gcashBtn.addEventListener('click', () => this.processGCashPayment());
        }
        if (mayaBtn) {
            mayaBtn.addEventListener('click', () => this.processMayaPayment());
        }
        if (cardBtn) {
            cardBtn.addEventListener('click', () => this.processCardPayment());
        }
    },

    async processGCashPayment() {
        const cart = this.getCart();
        if (cart.items.length === 0) {
           if (window.showCustomError) {
        window.showCustomError('Cart Empty', 'Please add items to cart before checkout');
    } else {
        alert('Cart is empty!');
    }
    return;
}

        this.showPaymentModal('GCash', 'Processing payment...');

        try {
            const response = await authenticatedFetch(`${PAYMENTS_API}/gcash/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    amount: cart.total,
                    items: cart.items.map(item => ({
                        id: item.id,
                        quantity: item.quantity,
                        price: item.price
                    })),
                    reference: `GCASH-${Date.now()}`,
                    discount_amount: cart.discountAmount || 0,
                    discount_type: cart.discountType || '',
                    discount_id_number: cart.discountIdNumber || '',
                })
            });

            const data = await response.json();

            if (data.success) {
                this.showSuccessModal('GCash', data);
                // Removed automatic close to allow receipt viewing/printing
            } else {
                this.showErrorModal('GCash', data.message);
            }
        } catch (error) {
            this.showErrorModal('GCash', error.message);
        }
    },

    async processMayaPayment() {
        const cart = this.getCart();
        if (cart.items.length === 0) {
           if (window.showCustomError) {
        window.showCustomError('Cart Empty', 'Please add items to cart before checkout');
    } else {
        alert('Cart is empty!');
    }
    return;
}

        this.showPaymentModal('Maya QR', 'Generating QR code...');

        try {
            const response = await authenticatedFetch(`${PAYMENTS_API}/maya/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    amount: cart.total,
                    items: cart.items.map(item => ({
                        id: item.id,
                        quantity: item.quantity,
                        price: item.price
                    })),
                    reference: `MAYA-${Date.now()}`,
                    discount_amount: cart.discountAmount || 0,
                    discount_type: cart.discountType || '',
                    discount_id_number: cart.discountIdNumber || '',
                })
            });

            const data = await response.json();

            if (data.success) {
                if (this.config.maya.use_mock_mode) {
                    this.showMockQRCode(data);
                    setTimeout(() => {
                        this.showSuccessModal('Maya', data);
                    }, 3000);
                }
            } else {
                this.showErrorModal('Maya', data.message);
            }
        } catch (error) {
            this.showErrorModal('Maya', error.message);
        }
    },

    async processCardPayment() {
        const cart = this.getCart();
        if (cart.items.length === 0) {
            if (window.showCustomError) {
                window.showCustomError('Cart Empty', 'Please add items to cart before checkout');
            } else {
                alert('Cart is empty!');
            }
            return;
        }

        this.showPaymentModal('Card Payment', 'Please insert/tap card...');

        try {
            const response = await authenticatedFetch(`${PAYMENTS_API}/card/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    amount: cart.total,
                    items: cart.items.map(item => ({
                        id: item.id,
                        quantity: item.quantity,
                        price: item.price
                    })),
                    reference: `CARD-${Date.now()}`
                })
            });

            const data = await response.json();

            if (data.success) {
                this.showSuccessModal('Card', data);
            } else {
                this.showErrorModal('Card', data.message);
            }
        } catch (error) {
            this.showErrorModal('Card', error.message);
        }
    },

    clearCartAndClose() {
        this.clearCart();
        this.closeModal();
    },

    showPaymentModal(method, message) {
        const modal = document.getElementById('payment-modal');
        if (modal) {
            modal.querySelector('.payment-method').textContent = method;
            modal.querySelector('.payment-message').textContent = message;
            modal.classList.remove('hidden');
        }
    },

    showMockQRCode(data) {
        const modal = document.getElementById('payment-modal');
        if (modal) {
            modal.querySelector('.payment-message').innerHTML = `
                <div class="text-center">
                    <div class="text-6xl mb-4">📱</div>
                    <p class="text-lg font-bold mb-2">Scan QR Code</p>
                    <div class="bg-white p-4 rounded inline-block">
                        <div class="w-48 h-48 bg-gray-800 flex items-center justify-center text-white text-xs">
                            MOCK QR CODE<br/>₱${data.amount}<br/>${data.maya_reference}
                        </div>
                    </div>
                    <p class="text-sm text-gray-500 mt-4">
                        (Demo Mode - Auto-completing in 3s...)
                    </p>
                </div>
            `;
        }
    },

    showSuccessModal(method, data) {
        const modal = document.getElementById('payment-modal');
        if (modal) {
            const cart = this.getCart();
            const timestamp = formatDT(new Date().toISOString());
            
            // Format line items for receipt
            const itemsHtml = cart.items.map(item => `
                <div class="flex justify-between text-sm mb-1">
                    <span>${item.name} x${item.quantity}</span>
                    <span class="font-mono">₱${(item.price * item.quantity).toFixed(2)}</span>
                </div>
                <div class="text-xs text-gray-500 mb-2 ml-2">@ ₱${item.price.toFixed(2)} each</div>
            `).join('');

            modal.querySelector('.payment-message').innerHTML = `
                <div class="receipt-container text-left bg-white border border-gray-200 p-4 rounded shadow-sm text-gray-800">
                    <div class="text-center border-bottom pb-4 mb-4 border-b border-dashed">
                        <div class="text-4xl mb-2">✅</div>
                        <p class="text-xl font-bold text-green-600">Payment Successful!</p>
                        <p class="text-xs text-gray-500 mt-1">${timestamp}</p>
                    </div>
                    
                    <div class="mb-4 border-b border-dashed pb-2">
                        <p class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-2">Items</p>
                        ${itemsHtml}
                    </div>
                    
                    <div class="mb-4 border-b border-dashed pb-4">
                        <div class="flex justify-between font-bold text-lg">
                            <span>TOTAL</span>
                            <span class="font-mono">₱${parseFloat(cart.total).toFixed(2)}</span>
                        </div>
                        <div class="flex justify-between text-sm mt-2">
                            <span>Paid via ${method}</span>
                            <span class="font-mono">₱${parseFloat(data.amount || cart.total).toFixed(2)}</span>
                        </div>
                        ${data.change_due ? `
                        <div class="flex justify-between text-sm mt-1 font-bold text-blue-600">
                            <span>Change Due</span>
                            <span class="font-mono">₱${parseFloat(data.change_due).toFixed(2)}</span>
                        </div>` : ''}
                    </div>

                    <div class="text-xs text-gray-500 space-y-1 mb-6">
                        <p><strong>Transaction:</strong> ${data.transaction_no || 'N/A'}</p>
                        ${data.card_type ? `<p><strong>Card:</strong> ${data.card_type} ****${data.last_4}</p>` : ''}
                        ${data.approval_code ? `<p><strong>Approval:</strong> ${data.approval_code}</p>` : ''}
                    </div>

                    <div class="grid grid-cols-2 gap-3 no-print">
                        <button id="thermal-print-btn" data-transaction-id="${data.transaction_id || ''}" onclick="thermalPrint()"
                                class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition">
                            <span>🖨️</span> Print Receipt
                        </button>
                        <button onclick="PaymentSystem.clearCartAndClose()" class="bg-gray-100 hover:bg-gray-200 text-gray-700 font-bold py-3 rounded-lg transition">
                            Done
                        </button>
                    </div>
                </div>
            `;

            // Auto-print receipt to thermal printer (fire-and-forget)
            if (data.transaction_id && window.autoPrintReceipt) {
                autoPrintReceipt(data.transaction_id);
            }
        }
    },

    showErrorModal(method, message) {
        const modal = document.getElementById('payment-modal');
        if (modal) {
            modal.querySelector('.payment-message').innerHTML = `
                <div class="text-center">
                    <div class="text-6xl mb-4">❌</div>
                    <p class="text-xl font-bold text-red-600 mb-4">Payment Failed</p>
                    <p class="text-gray-700 mb-4">${message}</p>
                    <button onclick="PaymentSystem.closeModal()" class="bg-gray-600 text-white px-6 py-2 rounded">
                        Try Again
                    </button>
                </div>
            `;
        }
    },

    closeModal() {
        const modal = document.getElementById('payment-modal');
        if (modal) {
            modal.classList.add('hidden');
        }
    },

    getCart() {
        return window.cart || { items: [], total: 0 };
    },

    clearCart() {
        window.cart.items = [];
        window.cart.total = 0;
        window.cart.discountAmount = 0;
        window.cart.discountType = '';
        window.cart.discountIdNumber = '';
        window.cart.discountLabel = '';
        window.cart.selectedDiscountType = null;
        window.cart.pendingDiscountType = null;
        if (window.updateCart) {
            window.updateCart();
        }
    }
};

// Initialize after slight delay
setTimeout(() => {
    PaymentSystem.init();
}, 500);
