document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('qhm-voucher-root');
  if (!root) return;

  const form = document.getElementById('qhm-coupon-form');
  const discountTypeSelect = form ? form.querySelector('[name="discount_type"]') : null;
  const valueInput = form ? form.querySelector('[name="value"]') : null;
  const valueLabel = form ? document.getElementById('qhm-value-label') : null;
  const valueHelp = form ? document.getElementById('qhm-value-help') : null;
  const capField = form ? document.getElementById('qhm-cap-field') : null;
  const capInput = form ? form.querySelector('[name="max_discount"]') : null;
  const scopeRadios = form ? form.querySelectorAll('[name="applies_to_all"]') : [];
  const productPicker = form ? document.getElementById('qhm-product-picker') : null;
  const minOrderInput = form ? form.querySelector('[name="min_order_amount"]') : null;
  const startsAtInput = form ? form.querySelector('[name="starts_at"]') : null;
  const endsAtInput = form ? form.querySelector('[name="ends_at"]') : null;
  const maxUsesInput = form ? form.querySelector('[name="max_uses"]') : null;
  const isActiveCheckbox = form ? form.querySelector('[name="is_active"]') : null;
  const summaryText = form ? document.getElementById('qhm-summary-text') : null;
  const btnReset = document.getElementById('qhm-btn-reset');

  const sidebar = document.getElementById('qhm-sidebar');
  const overlay = document.getElementById('qhm-overlay');
  const menuBtn = document.getElementById('qhm-menu-btn');
  const sidebarClose = document.getElementById('qhm-sidebar-close');

  if (menuBtn && typeof window.toggleQhmSidebar === 'function') {
    menuBtn.addEventListener('click', () => window.toggleQhmSidebar(true));
  } else if (menuBtn && sidebar && overlay) {
    menuBtn.addEventListener('click', () => {
      sidebar.classList.add('is-open');
      overlay.classList.add('is-open');
    });
  }

  const closeSidebar = () => {
    if (sidebar) sidebar.classList.remove('is-open');
    if (overlay) overlay.classList.remove('is-open');
  };

  if (sidebarClose) sidebarClose.addEventListener('click', closeSidebar);
  if (overlay) overlay.addEventListener('click', closeSidebar);

  const formatVND = (num) => {
    if (num === null || num === undefined || num === '' || isNaN(num)) return '0 đ';
    return new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2 }).format(Number(num)) + ' đ';
  };

  const formatDateDisplay = (dtStr) => {
    if (!dtStr) return '...';
    try {
      const dt = new Date(dtStr);
      if (isNaN(dt.getTime())) return dtStr;
      const pad = (n) => String(n).padStart(2, '0');
      return `${pad(dt.getDate())}/${pad(dt.getMonth() + 1)}/${dt.getFullYear()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
    } catch (e) {
      return dtStr;
    }
  };

  const syncDiscountType = () => {
    if (!discountTypeSelect) return;
    const isPercent = discountTypeSelect.value === 'PERCENT';

    if (valueLabel) {
      valueLabel.innerHTML = isPercent
        ? 'Mức giảm (%) <span class="qhm-req">*</span>'
        : 'Số tiền giảm (đ) <span class="qhm-req">*</span>';
    }

    if (valueHelp) {
      valueHelp.textContent = isPercent
        ? 'Lớn hơn 0 và không vượt quá 100%.'
        : 'Số tiền giảm cố định phải lớn hơn 0 đ.';
    }

    if (valueInput) {
      if (isPercent) {
        valueInput.max = '100';
        valueInput.placeholder = 'VD: 10';
      } else {
        valueInput.removeAttribute('max');
        valueInput.placeholder = 'VD: 500000';
      }
    }

    if (capField && capInput) {
      if (isPercent) {
        capField.style.display = 'flex';
        capInput.removeAttribute('disabled');
      } else {
        capField.style.display = 'none';
        capInput.setAttribute('disabled', 'disabled');
      }
    }
  };

  const syncProductScope = () => {
    let appliesToAll = true;
    scopeRadios.forEach((r) => {
      if (r.checked) {
        appliesToAll = r.value === 'True' || r.value === 'true' || r.value === '1';
      }
    });

    if (productPicker) {
      productPicker.style.display = appliesToAll ? 'none' : 'block';
    }
  };

  const updateSummary = () => {
    if (!summaryText) return;

    const isPercent = discountTypeSelect ? discountTypeSelect.value === 'PERCENT' : true;
    const val = valueInput ? valueInput.value.trim() : '';
    const cap = capInput ? capInput.value.trim() : '';
    const minOrder = minOrderInput ? minOrderInput.value.trim() : '';
    const starts = startsAtInput ? startsAtInput.value : '';
    const ends = endsAtInput ? endsAtInput.value : '';

    let scopeDesc = 'toàn bộ sản phẩm';
    let appliesToAll = true;
    scopeRadios.forEach((r) => {
      if (r.checked) appliesToAll = r.value === 'True' || r.value === 'true' || r.value === '1';
    });
    if (!appliesToAll && productPicker) {
      const checkedBoxes = productPicker.querySelectorAll('input[type="checkbox"]:checked');
      if (checkedBoxes.length > 0) {
        scopeDesc = `${checkedBoxes.length} sản phẩm chỉ định`;
      } else {
        scopeDesc = 'chưa chọn sản phẩm';
      }
    }

    let discountPart = 'Chưa thiết lập mức giảm';
    if (val) {
      if (isPercent) {
        discountPart = `Giảm ${val}%`;
        if (cap && Number(cap) > 0) {
          discountPart += ` (tối đa ${formatVND(cap)})`;
        }
      } else {
        discountPart = `Giảm ${formatVND(val)}`;
      }
    }

    let minOrderPart = '';
    if (minOrder && Number(minOrder) > 0) {
      minOrderPart = ` cho đơn hàng từ ${formatVND(minOrder)}`;
    } else {
      minOrderPart = ' không yêu cầu đơn tối thiểu';
    }

    let timePart = '';
    if (starts || ends) {
      timePart = ` Áp dụng từ ${formatDateDisplay(starts)} đến ${formatDateDisplay(ends)}.`;
    }

    summaryText.textContent = `${discountPart}${minOrderPart}. Phạm vi: ${scopeDesc}.${timePart}`;
  };

  if (discountTypeSelect) discountTypeSelect.addEventListener('change', () => { syncDiscountType(); updateSummary(); });
  if (valueInput) valueInput.addEventListener('input', updateSummary);
  if (capInput) capInput.addEventListener('input', updateSummary);
  if (minOrderInput) minOrderInput.addEventListener('input', updateSummary);
  if (startsAtInput) startsAtInput.addEventListener('change', updateSummary);
  if (endsAtInput) endsAtInput.addEventListener('change', updateSummary);
  scopeRadios.forEach((r) => r.addEventListener('change', () => { syncProductScope(); updateSummary(); }));
  if (productPicker) {
    productPicker.addEventListener('change', updateSummary);
  }

  syncDiscountType();
  syncProductScope();
  updateSummary();

  if (form) {
    const isEdit = form.dataset.isEdit === 'true';
    const couponPk = form.dataset.couponPk || 'new';
    const userId = form.dataset.userId || 'anon';
    const draftKey = `qhm_coupon_draft_${userId}_${isEdit ? 'edit' : 'create'}_${couponPk}`;
    const hasServerErrors = document.getElementById('qhm-server-errors') !== null;

    let saveTimeout = null;
    const saveDraft = () => {
      clearTimeout(saveTimeout);
      saveTimeout = setTimeout(() => {
        try {
          const selectedProducts = [];
          if (productPicker) {
            productPicker.querySelectorAll('input[type="checkbox"]:checked').forEach((cb) => {
              selectedProducts.push(cb.value);
            });
          }

          let scopeVal = 'True';
          scopeRadios.forEach((r) => {
            if (r.checked) scopeVal = r.value;
          });

          const draftData = {
            code: form.querySelector('[name="code"]') ? form.querySelector('[name="code"]').value : '',
            discount_type: discountTypeSelect ? discountTypeSelect.value : 'PERCENT',
            value: valueInput ? valueInput.value : '',
            max_discount: capInput ? capInput.value : '',
            min_order_amount: minOrderInput ? minOrderInput.value : '',
            max_uses: maxUsesInput ? maxUsesInput.value : '',
            starts_at: startsAtInput ? startsAtInput.value : '',
            ends_at: endsAtInput ? endsAtInput.value : '',
            is_active: isActiveCheckbox ? isActiveCheckbox.checked : true,
            applies_to_all: scopeVal,
            products: selectedProducts,
            savedAt: Date.now(),
          };
          sessionStorage.setItem(draftKey, JSON.stringify(draftData));
        } catch (e) {
        }
      }, 300);
    };

    if (!hasServerErrors) {
      try {
        const raw = sessionStorage.getItem(draftKey);
        if (raw) {
          const data = JSON.parse(raw);
          if (data && typeof data === 'object') {
            const codeEl = form.querySelector('[name="code"]');
            if (codeEl && data.code !== undefined) codeEl.value = data.code;

            if (discountTypeSelect && data.discount_type !== undefined) discountTypeSelect.value = data.discount_type;
            if (valueInput && data.value !== undefined) valueInput.value = data.value;
            if (capInput && data.max_discount !== undefined) capInput.value = data.max_discount;
            if (minOrderInput && data.min_order_amount !== undefined) minOrderInput.value = data.min_order_amount;
            if (maxUsesInput && data.max_uses !== undefined) maxUsesInput.value = data.max_uses;
            if (startsAtInput && data.starts_at !== undefined) startsAtInput.value = data.starts_at;
            if (endsAtInput && data.ends_at !== undefined) endsAtInput.value = data.ends_at;
            if (isActiveCheckbox && data.is_active !== undefined) isActiveCheckbox.checked = Boolean(data.is_active);

            if (data.applies_to_all !== undefined) {
              scopeRadios.forEach((r) => {
                r.checked = r.value === String(data.applies_to_all);
              });
            }

            if (Array.isArray(data.products) && productPicker) {
              productPicker.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
                cb.checked = data.products.includes(cb.value);
              });
            }

            syncDiscountType();
            syncProductScope();
            updateSummary();
          }
        }
      } catch (e) {
      }
    }

    form.addEventListener('input', saveDraft);
    form.addEventListener('change', saveDraft);

    form.addEventListener('submit', () => {
      try {
        sessionStorage.removeItem(draftKey);
      } catch (e) {}
    });

    if (btnReset) {
      btnReset.addEventListener('click', () => {
        try {
          sessionStorage.removeItem(draftKey);
        } catch (e) {}

        if (isEdit) {
          form.reset();
        } else {
          form.reset();
          if (valueInput) valueInput.value = '';
          if (capInput) capInput.value = '';
          if (minOrderInput) minOrderInput.value = '0';
          if (isActiveCheckbox) isActiveCheckbox.checked = true;
          scopeRadios.forEach((r) => {
            if (r.value === 'True' || r.value === 'true' || r.value === '1') r.checked = true;
          });
        }
        syncDiscountType();
        syncProductScope();
        updateSummary();
      });
    }
  }
});

