/**
 * Quốc Hùng Mobile — Business Analytics Dashboard JavaScript
 * Quản lý khởi tạo biểu đồ (Chart.js & SVG Fallback), tương tác bộ lọc thời gian,
 * chuyển tab sản phẩm, điều khiển sidebar di động và modal.
 */

// Bảng màu chuẩn danh mục theo đặc tả (Yêu cầu 7)
const CATEGORY_COLORS = [
    "#E62E2D",
    "#1F2A24",
    "#9CA3AF",
    "#F59E0B",
    "#2563EB",
    "#8B5CF6",
    "#EC4899"
];

// Định dạng tiền tệ VNĐ
function formatVND(value) {
    if (value === null || value === undefined) return '0 đ';
    return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(value);
}

// Định dạng số nguyên chuẩn VN
function formatVNNumber(value) {
    if (value === null || value === undefined) return '0';
    return new Intl.NumberFormat('vi-VN').format(Math.round(value));
}

// 1. Quản lý Sidebar di động
function toggleQhmSidebar(forceState) {
    const sidebar = document.getElementById('qhm-mgmt-sidebar');
    const backdrop = document.getElementById('qhm-sidebar-backdrop');
    const toggleBtn = document.getElementById('qhm-sidebar-toggle');
    if (!sidebar || !backdrop) return;

    const isOpen = typeof forceState === 'boolean' ? forceState : !sidebar.classList.contains('open');
    if (isOpen) {
        sidebar.classList.add('open');
        backdrop.classList.add('open');
        if (toggleBtn) toggleBtn.setAttribute('aria-expanded', 'true');
        const closeBtn = document.getElementById('qhm-sidebar-close');
        if (closeBtn) setTimeout(() => closeBtn.focus(), 50);
    } else {
        sidebar.classList.remove('open');
        backdrop.classList.remove('open');
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', 'false');
            toggleBtn.focus();
        }
    }
}

document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        const sidebar = document.getElementById('qhm-mgmt-sidebar');
        if (sidebar && sidebar.classList.contains('open')) {
            toggleQhmSidebar(false);
        }
        toggleReturnDetailsModal(false);
    }
});

// 2. Chuyển đổi Tab Sản phẩm bán chạy / bán chậm
function switchProductTab(tabName) {
    const bestBtn = document.getElementById('tab-btn-best-selling');
    const slowBtn = document.getElementById('tab-btn-slowest-selling');
    const bestTable = document.getElementById('product-tab-best');
    const slowTable = document.getElementById('product-tab-slowest');

    if (tabName === 'best') {
        if (bestBtn) bestBtn.classList.add('active');
        if (slowBtn) slowBtn.classList.remove('active');
        if (bestTable) bestTable.style.display = 'block';
        if (slowTable) slowTable.style.display = 'none';
    } else {
        if (slowBtn) slowBtn.classList.add('active');
        if (bestBtn) bestBtn.classList.remove('active');
        if (bestTable) bestTable.style.display = 'none';
        if (slowTable) slowTable.style.display = 'block';
    }
}

// 3. Quản lý Modal chi tiết đổi trả
function toggleReturnDetailsModal(show) {
    const modal = document.getElementById('qhm-return-details-modal');
    if (modal) {
        modal.style.display = show ? 'flex' : 'none';
    }
}

// 4. Mở rộng bộ chọn ngày tùy chọn (Custom Date Range)
function toggleCustomDateRange(show) {
    const customSection = document.getElementById('qhm-custom-date-section');
    const customBtn = document.getElementById('filter-btn-custom');
    if (!customSection) return;

    if (show) {
        customSection.style.display = 'flex';
        const startInput = document.getElementById('input-start-date');
        if (startInput) {
            startInput.focus();
        }
        if (customBtn) {
            customBtn.classList.add('active');
        }
        // Gán hidden input
        const periodInput = document.getElementById('filter-period-val');
        if (periodInput) periodInput.value = 'custom';
    } else {
        customSection.style.display = 'none';
    }
}

// ==========================================================================
// KHỞI TẠO BIỂU ĐỒ (CHART.JS VÀ SVG FALLBACK)
// ==========================================================================

let revChartInstance = null;
let catChartInstance = null;
let globalRevData = null;
let globalCatData = null;

function initCharts() {
    // 1. Biểu đồ Doanh thu theo Ngày (Revenue by Day Line Chart)
    const revDataEl = document.getElementById('revenue-by-day-data');
    if (revDataEl) {
        try {
            let data = JSON.parse(revDataEl.textContent);
            if (typeof data === 'string') data = JSON.parse(data);
            globalRevData = data;
            renderRevenueChart(data);
        } catch (e) {
            console.error('Lỗi khi nạp dữ liệu biểu đồ doanh thu:', e);
        }
    }

    // 2. Biểu đồ Tỷ trọng Doanh thu theo Danh mục (Category Donut Chart)
    const catDataEl = document.getElementById('category-revenue-data');
    if (catDataEl) {
        try {
            let data = JSON.parse(catDataEl.textContent);
            if (typeof data === 'string') data = JSON.parse(data);
            globalCatData = data;
            renderCategoryChart(data);
        } catch (e) {
            console.error('Lỗi khi nạp dữ liệu biểu đồ danh mục:', e);
        }
    }
}

// Vẽ biểu đồ Doanh thu (Chart.js hoặc SVG Fallback)
function renderRevenueChart(data) {
    const canvas = document.getElementById('revenueByDayChart');
    const wrapper = document.getElementById('revenue-chart-wrapper');
    if (!wrapper || !canvas) return;

    if (!data || !data.labels || data.labels.length === 0) {
        wrapper.innerHTML = `
            <div class="qhm-dash-empty-state">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="1.5">
                    <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
                    <line x1="8" y1="21" x2="16" y2="21"></line>
                    <line x1="12" y1="17" x2="12" y2="21"></line>
                </svg>
                <p>Chưa có dữ liệu doanh thu trong khoảng thời gian này.</p>
            </div>`;
        return;
    }

    if (typeof Chart !== 'undefined') {
        if (revChartInstance) {
            revChartInstance.destroy();
        }
        const ctx = canvas.getContext('2d');
        revChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [
                    {
                        label: 'Kỳ này',
                        data: data.current_revenues,
                        borderColor: '#E62E2D',
                        backgroundColor: 'rgba(230, 46, 45, 0.10)',
                        borderWidth: 2.5,
                        fill: true,
                        tension: 0.35,
                        pointRadius: 3,
                        pointHoverRadius: 6,
                        pointBackgroundColor: '#E62E2D',
                    },
                    {
                        label: 'Kỳ trước',
                        data: data.previous_revenues,
                        borderColor: '#6B7280',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        fill: false,
                        tension: 0.35,
                        pointRadius: 2,
                        pointHoverRadius: 5,
                        pointBackgroundColor: '#6B7280',
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false,
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        align: 'end',
                        labels: {
                            boxWidth: 16,
                            font: { family: 'Inter', size: 12, weight: '500' },
                            color: '#111827'
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1F2A24',
                        titleColor: '#FFFFFF',
                        bodyColor: '#FFFFFF',
                        titleFont: { family: 'Inter', size: 12, weight: '600' },
                        bodyFont: { family: 'Inter', size: 13 },
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (context) {
                                let label = context.dataset.label || '';
                                if (label) label += ': ';
                                if (context.parsed.y !== null) {
                                    label += formatVND(context.parsed.y);
                                }
                                return label;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            color: '#6B7280',
                            font: { family: 'Inter', size: 11 },
                            callback: function (val) {
                                if (val >= 1000000000) {
                                    return (val / 1000000000).toFixed(1) + ' Tỷ';
                                }
                                if (val >= 1000000) {
                                    return (val / 1000000).toFixed(0) + ' Tr';
                                }
                                return val.toLocaleString('vi-VN');
                            }
                        },
                        grid: {
                            color: '#E5E7EB',
                            drawBorder: false,
                        }
                    },
                    x: {
                        ticks: {
                            color: '#6B7280',
                            font: { family: 'Inter', size: 11 },
                            maxRotation: 0,
                        },
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    } else {
        // Fallback SVG an toàn khi không có mạng hoặc Chart.js không tải được
        renderSvgLineChartFallback(canvas, data);
    }
}

// Fallback SVG Line Chart
function renderSvgLineChartFallback(canvas, data) {
    const container = canvas.parentElement;
    if (!container) return;

    // Ẩn canvas cũ và xóa SVG fallback trước đó (nếu có)
    canvas.style.display = 'none';
    const oldSvg = container.querySelector('svg.qhm-svg-fallback-chart');
    if (oldSvg) oldSvg.remove();

    const w = container.clientWidth || 600;
    const h = container.clientHeight || 300;
    const padL = 70, padR = 24, padT = 24, padB = 40;
    const chartW = Math.max(100, w - padL - padR);
    const chartH = Math.max(100, h - padT - padB);

    const allVals = [...(data.current_revenues || []), ...(data.previous_revenues || [])];
    const maxVal = Math.max(1, ...allVals);
    const n = data.labels.length;
    const stepX = n > 1 ? chartW / (n - 1) : chartW;

    let currPoints = (data.current_revenues || []).map((v, i) => {
        const x = padL + i * stepX;
        const y = padT + chartH - (v / maxVal) * chartH;
        return { x, y, v };
    });

    let prevPoints = (data.previous_revenues || []).map((v, i) => {
        const x = padL + i * stepX;
        const y = padT + chartH - (v / maxVal) * chartH;
        return { x, y, v };
    });

    const currLineD = currPoints.reduce((acc, p, i) => acc + (i === 0 ? `M ${p.x} ${p.y}` : ` L ${p.x} ${p.y}`), '');
    const currAreaD = currPoints.length > 0
        ? currLineD + ` L ${currPoints[currPoints.length - 1].x} ${padT + chartH} L ${currPoints[0].x} ${padT + chartH} Z`
        : '';
    const prevLineD = prevPoints.reduce((acc, p, i) => acc + (i === 0 ? `M ${p.x} ${p.y}` : ` L ${p.x} ${p.y}`), '');

    let svg = `<svg class="qhm-svg-fallback-chart" viewBox="0 0 ${w} ${h}" style="width: 100%; height: 100%; display: block;" xmlns="http://www.w3.org/2000/svg">`;
    svg += `
        <defs>
            <linearGradient id="qhmRevGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#E62E2D" stop-opacity="0.18"/>
                <stop offset="100%" stop-color="#E62E2D" stop-opacity="0.01"/>
            </linearGradient>
        </defs>`;

    // Gridlines ngang & Nhãn trục Y
    for (let i = 0; i <= 4; i++) {
        const y = padT + (chartH / 4) * i;
        const v = Math.round(maxVal - (maxVal / 4) * i);
        let label = '';
        if (v >= 1000000000) label = (v / 1000000000).toFixed(1) + ' Tỷ';
        else if (v >= 1000000) label = (v / 1000000).toFixed(0) + ' Tr';
        else label = v.toLocaleString('vi-VN');

        svg += `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="#E5E7EB" stroke-width="1"/>`;
        svg += `<text x="${padL - 10}" y="${y + 4}" font-family="Inter, sans-serif" font-size="11" fill="#6B7280" text-anchor="end">${label}</text>`;
    }

    // Đồ thị diện tích & đường line
    if (currAreaD) {
        svg += `<path d="${currAreaD}" fill="url(#qhmRevGradient)"/>`;
    }
    if (prevLineD) {
        svg += `<path d="${prevLineD}" fill="none" stroke="#6B7280" stroke-width="2" stroke-dasharray="5,5"/>`;
    }
    if (currLineD) {
        svg += `<path d="${currLineD}" fill="none" stroke="#E62E2D" stroke-width="2.5"/>`;
    }

    // Điểm nút dữ liệu kỳ này
    currPoints.forEach((p, idx) => {
        svg += `
            <circle cx="${p.x}" cy="${p.y}" r="3.5" fill="#FFFFFF" stroke="#E62E2D" stroke-width="2">
                <title>Kỳ này (${data.labels[idx]}): ${formatVND(p.v)}</title>
            </circle>`;
    });

    // Nhãn trục X
    const stepLabel = Math.max(1, Math.floor(n / 7));
    data.labels.forEach((lb, i) => {
        if (i % stepLabel === 0 || i === n - 1) {
            const x = padL + i * stepX;
            svg += `<text x="${x}" y="${h - 12}" font-family="Inter, sans-serif" font-size="11" fill="#6B7280" text-anchor="middle">${lb}</text>`;
        }
    });

    svg += `</svg>`;
    container.insertAdjacentHTML('beforeend', svg);
}

// Vẽ biểu đồ Danh mục (Chart.js hoặc SVG Fallback)
function renderCategoryChart(data) {
    const canvas = document.getElementById('categoryRevenueChart');
    const wrapper = document.getElementById('category-chart-wrapper');
    if (!wrapper || !canvas) return;

    if (!data || !data.categories || data.categories.length === 0 || data.total_revenue_float <= 0) {
        wrapper.innerHTML = `
            <div class="qhm-dash-empty-state">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="1.5">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M12 2a10 10 0 0 1 10 10H12V2z"></path>
                </svg>
                <p>Chưa có dữ liệu doanh thu danh mục trong khoảng thời gian này.</p>
            </div>`;
        return;
    }

    const bgColors = data.categories.map((_, i) => CATEGORY_COLORS[i % CATEGORY_COLORS.length]);

    // Gán màu cho legend dots bên dưới
    const dots = document.querySelectorAll('.qhm-dash-cat-dot');
    dots.forEach((dot, idx) => {
        dot.style.backgroundColor = bgColors[idx % bgColors.length];
    });

    if (typeof Chart !== 'undefined') {
        if (catChartInstance) {
            catChartInstance.destroy();
        }
        const ctx = canvas.getContext('2d');
        catChartInstance = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: data.labels,
                datasets: [{
                    data: data.revenues,
                    backgroundColor: bgColors,
                    borderWidth: 2,
                    borderColor: '#FFFFFF',
                    hoverOffset: 6,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '72%',
                plugins: {
                    legend: {
                        display: false,
                    },
                    tooltip: {
                        backgroundColor: '#1F2A24',
                        titleColor: '#FFFFFF',
                        bodyColor: '#FFFFFF',
                        titleFont: { family: 'Inter', size: 12, weight: '600' },
                        bodyFont: { family: 'Inter', size: 13 },
                        padding: 10,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (context) {
                                const cat = data.categories[context.dataIndex];
                                const formatted = formatVND(context.raw);
                                return ` ${context.label}: ${formatted} (${cat.percentage}%)`;
                            }
                        }
                    }
                }
            }
        });
    } else {
        // Fallback SVG an toàn
        renderSvgDonutChartFallback(canvas, data, bgColors);
    }
}

// Fallback SVG Donut Chart
function renderSvgDonutChartFallback(canvas, data, colors) {
    const container = canvas.parentElement;
    if (!container) return;

    canvas.style.display = 'none';
    const oldSvg = container.querySelector('svg.qhm-svg-fallback-donut');
    if (oldSvg) oldSvg.remove();

    const w = container.clientWidth || 300;
    const h = container.clientHeight || 300;
    const cx = w / 2, cy = h / 2;
    const r = Math.min(cx, cy) - 16;
    const innerR = r * 0.72;

    let svg = `<svg class="qhm-svg-fallback-donut" viewBox="0 0 ${w} ${h}" style="width: 100%; height: 100%; display: block;" xmlns="http://www.w3.org/2000/svg">`;
    let startAngle = -Math.PI / 2;
    const total = data.total_revenue_float;

    data.categories.forEach((cat, idx) => {
        const sliceAngle = (cat.revenue_float / total) * Math.PI * 2;
        const endAngle = startAngle + sliceAngle;

        const x1 = cx + r * Math.cos(startAngle);
        const y1 = cy + r * Math.sin(startAngle);
        const x2 = cx + r * Math.cos(endAngle);
        const y2 = cy + r * Math.sin(endAngle);

        const x3 = cx + innerR * Math.cos(endAngle);
        const y3 = cy + innerR * Math.sin(endAngle);
        const x4 = cx + innerR * Math.cos(startAngle);
        const y4 = cy + innerR * Math.sin(startAngle);

        const largeArc = sliceAngle > Math.PI ? 1 : 0;
        const pathD = `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} L ${x3} ${y3} A ${innerR} ${innerR} 0 ${largeArc} 0 ${x4} ${y4} Z`;

        svg += `
            <path d="${pathD}" fill="${colors[idx % colors.length]}" stroke="#FFFFFF" stroke-width="2">
                <title>${cat.name}: ${formatVND(cat.revenue_float)} (${cat.percentage}%)</title>
            </path>`;
        startAngle = endAngle;
    });

    // Vẽ nhãn tổng doanh thu trung tâm
    svg += `
        <text x="${cx}" y="${cy - 8}" font-family="Inter, sans-serif" font-size="11" font-weight="600" fill="#6B7280" text-anchor="middle" letter-spacing="0.5">TỔNG DOANH THU</text>
        <text x="${cx}" y="${cy + 16}" font-family="Inter, sans-serif" font-size="16" font-weight="800" fill="#111827" text-anchor="middle">${formatVND(total)}</text>
    `;

    svg += `</svg>`;
    container.insertAdjacentHTML('beforeend', svg);
}

// Lắng nghe thay đổi kích thước cửa sổ để vẽ lại biểu đồ SVG fallback
window.addEventListener('resize', function () {
    if (typeof Chart === 'undefined') {
        if (globalRevData) {
            const canvasRev = document.getElementById('revenueByDayChart');
            if (canvasRev) renderSvgLineChartFallback(canvasRev, globalRevData);
        }
        if (globalCatData) {
            const canvasCat = document.getElementById('categoryRevenueChart');
            if (canvasCat) {
                const bgColors = globalCatData.categories.map((_, i) => CATEGORY_COLORS[i % CATEGORY_COLORS.length]);
                renderSvgDonutChartFallback(canvasCat, globalCatData, bgColors);
            }
        }
    }
});

// Khởi tạo toàn bộ khi DOM tải xong
document.addEventListener('DOMContentLoaded', function () {
    initCharts();
});
