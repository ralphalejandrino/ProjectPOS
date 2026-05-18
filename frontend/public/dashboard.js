// API_URL is defined in config.js (loaded before this script)

// Minimal alert helper — matches the dynamic modal pattern used in viewReceipt()
function showDashboardAlert(message) {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4';
    modal.style.zIndex = 'var(--z-modal)';
    modal.innerHTML = `
        <div class="bg-white rounded-2xl shadow-2xl max-w-sm w-full">
            <div class="bg-gradient-to-r from-blue-500 to-blue-600 text-white p-5 text-center rounded-t-2xl">
                <h2 class="text-lg font-bold">Notice</h2>
            </div>
            <div class="p-6 text-center text-gray-700">${message}</div>
            <div class="px-6 pb-6">
                <button onclick="this.closest('.fixed').remove()"
                        class="w-full bg-blue-600 text-white py-3 rounded-lg font-bold hover:bg-blue-700">
                    OK
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
}

let allTransactions = [];
let revenueChart = null;
let topItemsChart = null;

// Tab switching
function showTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.add('hidden');
    });

    // Remove active state from all tab buttons
    document.querySelectorAll('[id^="tab-"]').forEach(btn => {
        btn.classList.remove('text-blue-600', 'border-b-2', 'border-blue-600');
        btn.classList.add('text-gray-500');
    });

    // Show selected tab
    const contentEl = document.getElementById(`content-${tabName}`);
    if (!contentEl) { console.warn(`showTab: element #content-${tabName} not found`); return; }
    contentEl.classList.remove('hidden');

    // Activate selected tab button
    const activeBtn = document.getElementById(`tab-${tabName}`);
    if (!activeBtn) { console.warn(`showTab: element #tab-${tabName} not found`); return; }
    activeBtn.classList.add('text-blue-600', 'border-b-2', 'border-blue-600');
    activeBtn.classList.remove('text-gray-500');

    // Load data for the tab
    if (tabName === 'history') {
        loadTransactionHistory();
    }
}

// Load main dashboard (ORIGINAL WORKING CODE)
async function loadDashboard() {
    try {
        // Fetch transactions
        const response = await authenticatedFetch(`${API_URL}/transactions/`);
        const transactionsRaw = await response.json();
        allTransactions = transactionsRaw.results !== undefined ? transactionsRaw.results : transactionsRaw;
        const transactions = allTransactions;

        // Calculate daily sales for last 30 days
        const last30Days = [];
        const today = new Date();
        for (let i = 29; i >= 0; i--) {
            const date = new Date(today);
            date.setDate(date.getDate() - i);
            const dateStr = date.toISOString().split('T')[0];

            const dayTransactions = transactions.filter(t => {
                const txnDate = new Date(t.created_at).toISOString().split('T')[0];
                return txnDate === dateStr;
            });

            const total = dayTransactions.reduce((sum, t) => sum + parseFloat(t.total_amount), 0);
            const count = dayTransactions.length;

            last30Days.push({
                date: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
                total: total,
                count: count
            });
        }

        // Revenue chart
        if (revenueChart) { revenueChart.destroy(); revenueChart = null; }
        const revenueCanvas = document.getElementById('revenueChart');
        if (!revenueCanvas) { console.warn('revenueChart canvas not found'); return; }
        const revenueCtx = revenueCanvas.getContext('2d');
        revenueChart = new Chart(revenueCtx, {
            type: 'line',
            data: {
                labels: last30Days.map(d => d.date),
                datasets: [{
                    label: 'Daily Revenue',
                    data: last30Days.map(d => d.total),
                    borderColor: 'rgb(59, 130, 246)',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    tension: 0.4,
                    fill: true,
                    borderWidth: 3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (context) => `₱${context.parsed.y.toFixed(2)}`
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: value => '₱' + value.toLocaleString()
                        }
                    }
                }
            }
        });

        // Use real top items data from dashboard API
        const dashResponse = await authenticatedFetch(`${API_URL}/dashboard/`);
        const dashData = await dashResponse.json();
        const topItems = dashData.top_items || [];

        if (topItemsChart) { topItemsChart.destroy(); topItemsChart = null; }
        const topItemsCanvas = document.getElementById('topItemsChart');
        if (!topItemsCanvas) { console.warn('topItemsChart canvas not found'); return; }
        if (!topItems || topItems.length === 0) {
            topItemsCanvas.classList.add('hidden');
            let noDataMsg = topItemsCanvas.parentElement.querySelector('.no-sales-msg');
            if (!noDataMsg) {
                noDataMsg = document.createElement('div');
                noDataMsg.className = 'no-sales-msg flex items-center justify-center text-gray-400 text-sm py-8';
                noDataMsg.textContent = 'No sales data';
                topItemsCanvas.parentElement.appendChild(noDataMsg);
            }
            noDataMsg.classList.remove('hidden');
            return;
        }
        // Hide any previous "No sales data" message and restore canvas
        topItemsCanvas.classList.remove('hidden');
        const existingMsg = topItemsCanvas.parentElement.querySelector('.no-sales-msg');
        if (existingMsg) existingMsg.classList.add('hidden');
        const itemsCtx = topItemsCanvas.getContext('2d');
        topItemsChart = new Chart(itemsCtx, {
            type: 'doughnut',
            data: {
                labels: topItems.map(item => item.name),
                datasets: [{
                    data: topItems.map(item => item.quantity),
                    backgroundColor: [
                        'rgba(34, 197, 94, 0.8)',
                        'rgba(59, 130, 246, 0.8)'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });

    } catch (error) {
    }
}

// Load transaction history
async function loadTransactionHistory() {
    try {
        const response = await authenticatedFetch(`${API_URL}/transactions/`);
        const historyRaw = await response.json();
        allTransactions = historyRaw.results !== undefined ? historyRaw.results : historyRaw;
        renderTransactionHistory(allTransactions);
    } catch (error) {
    }
}

function renderTransactionHistory(transactions) {
    const tbody = document.getElementById('history-table-body');
    if (!tbody) { console.warn('renderTransactionHistory: #history-table-body not found'); return; }
    
    if (transactions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-8 text-gray-500">No transactions found</td></tr>';
        return;
    }
    
    tbody.innerHTML = transactions.slice(0, 50).map(txn => `
        <tr class="hover:bg-gray-50">
            <td class="px-4 py-3 font-mono text-sm">${txn.transaction_no || 'N/A'}</td>
            <td class="px-4 py-3 text-sm">${new Date(txn.created_at).toLocaleString()}</td>
            <td class="px-4 py-3">
                <span class="px-2 py-1 rounded-full text-xs font-semibold ${
                    txn.payment_method === 'cash' ? 'bg-green-100 text-green-700' : 'bg-blue-100 text-blue-700'
                }">
                    ${txn.payment_method === 'cash' ? '💵 Cash' : '💙 GCash'}
                </span>
            </td>
            <td class="px-4 py-3 text-right font-bold">₱${parseFloat(txn.total_amount).toFixed(2)}</td>
            <td class="px-4 py-3 text-center">
                <button onclick="viewReceipt('${txn.id}')" 
                        class="text-blue-600 hover:text-blue-800 font-semibold text-sm">
                    View Receipt
                </button>
            </td>
        </tr>
    `).join('');
}

function filterHistory() {
    const dateInput = document.getElementById('history-date-filter');
    if (!dateInput) { console.warn('filterHistory: #history-date-filter not found'); return; }
    const selectedDate = dateInput.value;
    
    if (!selectedDate) {
        renderTransactionHistory(allTransactions);
        return;
    }
    
    const filtered = allTransactions.filter(txn => {
        const txnDate = new Date(txn.created_at).toISOString().split('T')[0];
        return txnDate === selectedDate;
    });
    
    renderTransactionHistory(filtered);
}

async function viewReceipt(transactionId) {
    try {
        const response = await authenticatedFetch(`${API_URL}/transactions/${transactionId}/`);
        const txn = await response.json();
        
        // Create modal
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4';
    modal.style.zIndex = 'var(--z-modal)';
        modal.innerHTML = `
            <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full max-h-[80vh] overflow-y-auto">
                <div class="bg-gradient-to-r from-blue-500 to-blue-600 text-white p-6 text-center">
                    <h2 class="text-2xl font-bold">🧾 Receipt</h2>
                    <p class="text-sm text-blue-100 mt-1">${txn.transaction_no}</p>
                </div>
                <div class="p-6">
                    <div class="mb-4 pb-4 border-b">
                        <p class="text-sm text-gray-500">Date: ${new Date(txn.created_at).toLocaleString()}</p>
                        <p class="text-sm text-gray-500">Payment: ${{cash:'Cash',gcash:'GCash',maya:'Maya',card:'Card'}[txn.payment_method] || txn.payment_method}</p>
                    </div>
                    <div class="space-y-3 mb-4">
                        ${txn.items ? txn.items.map(item => `
                            <div class="flex justify-between">
                                <span>${item.item_name} x${item.quantity}</span>
                                <span class="font-semibold">₱${parseFloat(item.subtotal).toFixed(2)}</span>
                            </div>
                            ${item.variant_selections && item.variant_selections.length > 0 ? item.variant_selections.map(v => `
                                <div class="flex justify-between text-sm text-gray-500 pl-4">
                                    <span>${v.group_name}: ${v.option_name}</span>
                                    <span>${parseFloat(v.price_modifier) !== 0 ? (parseFloat(v.price_modifier) > 0 ? '+' : '') + '₱' + parseFloat(v.price_modifier).toFixed(2) : ''}</span>
                                </div>
                            `).join('') : ''}
                        `).join('') : '<p class="text-gray-500 text-sm">Items not available</p>'}
                    </div>
                    ${txn.discount_amount > 0 ? `
                    <div class="pb-4 border-b mb-4">
                        <div class="flex justify-between text-sm text-gray-600">
                            <span>Discount (${txn.discount_type || 'applied'}${txn.discount_id_number ? ' — ' + txn.discount_id_number : ''}):</span>
                            <span class="text-red-600">-₱${parseFloat(txn.discount_amount).toFixed(2)}</span>
                        </div>
                    </div>
                    ` : ''}
                    <div class="pt-4 border-t">
                        <div class="flex justify-between text-xl font-bold">
                            <span>Total:</span>
                            <span class="text-blue-600">₱${parseFloat(txn.total_amount).toFixed(2)}</span>
                        </div>
                    </div>
                    <button onclick="this.closest('.fixed').remove()"
                            class="w-full mt-6 bg-blue-600 text-white py-3 rounded-lg font-bold hover:bg-blue-700">
                        Close
                    </button>
                </div>
            </div>
        `;
        
        document.body.appendChild(modal);
        modal.onclick = (e) => {
            if (e.target === modal) modal.remove();
        };
    } catch (error) {
        showDashboardAlert('Error loading receipt details');
    }
}

async function loadDailyReport() {
    const dateInput = document.getElementById('report-date');
    if (!dateInput) { console.warn('loadDailyReport: #report-date not found'); return; }
    const selectedDate = dateInput.value;
    
    if (!selectedDate) {
        showDashboardAlert('Please select a date');
        return;
    }
    
    const filtered = allTransactions.filter(txn => {
        const txnDate = new Date(txn.created_at).toISOString().split('T')[0];
        return txnDate === selectedDate;
    });
    
    const total = filtered.reduce((sum, txn) => sum + parseFloat(txn.total_amount), 0);
    const cash = filtered.filter(t => t.payment_method === 'cash').reduce((sum, t) => sum + parseFloat(t.total_amount), 0);
    const gcash = filtered.filter(t => t.payment_method === 'gcash').reduce((sum, t) => sum + parseFloat(t.total_amount), 0);
    
    const content = document.getElementById('daily-report-content');
    if (!content) { console.warn('loadDailyReport: #daily-report-content not found'); return; }
    content.innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
            <div class="bg-blue-50 rounded-lg p-6">
                <p class="text-sm text-gray-600 mb-1">Total Sales</p>
                <p class="text-3xl font-bold text-blue-600">₱${total.toFixed(2)}</p>
                <p class="text-xs text-gray-500 mt-1">${filtered.length} transactions</p>
            </div>
            <div class="bg-green-50 rounded-lg p-6">
                <p class="text-sm text-gray-600 mb-1">💵 Cash Payments</p>
                <p class="text-3xl font-bold text-green-600">₱${cash.toFixed(2)}</p>
                <p class="text-xs text-gray-500 mt-1">${filtered.filter(t => t.payment_method === 'cash').length} transactions</p>
            </div>
            <div class="bg-purple-50 rounded-lg p-6">
                <p class="text-sm text-gray-600 mb-1">💙 GCash Payments</p>
                <p class="text-3xl font-bold text-purple-600">₱${gcash.toFixed(2)}</p>
                <p class="text-xs text-gray-500 mt-1">${filtered.filter(t => t.payment_method === 'gcash').length} transactions</p>
            </div>
        </div>
        <div class="bg-gray-50 rounded-lg p-6">
            <h3 class="font-bold text-gray-800 mb-3 text-lg">📊 Summary for ${new Date(selectedDate).toLocaleDateString()}</h3>
            <div class="space-y-2">
                <div class="flex justify-between">
                    <span class="text-gray-600">Total Transactions:</span>
                    <span class="font-bold">${filtered.length}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-600">Average Transaction:</span>
                    <span class="font-bold">₱${(isFinite(total / filtered.length) ? total / filtered.length : 0).toFixed(2)}</span>
                </div>
                <div class="flex justify-between">
                    <span class="text-gray-600">Cash vs GCash Ratio:</span>
                    <span class="font-bold">${((cash / total * 100) || 0).toFixed(0)}% / ${((gcash / total * 100) || 0).toFixed(0)}%</span>
                </div>
            </div>
        </div>
    `;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    
    // Set today's date as default for reports
    const today = new Date().toISOString().split('T')[0];
    const reportDate = document.getElementById('report-date');
    if (reportDate) reportDate.value = today;
    
    const historyDate = document.getElementById('history-date-filter');
    if (historyDate) historyDate.value = '';
});
