document.addEventListener('DOMContentLoaded', () => {
  const mainImage = document.querySelector('#mainProductImage img');
  document.querySelectorAll('[data-gallery-src]').forEach((button) => button.addEventListener('click', () => {
    if (mainImage) mainImage.src = button.dataset.gallerySrc;
    document.querySelectorAll('[data-gallery-src]').forEach((item) => item.classList.toggle('active', item === button));
  }));

  document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach((item) => item.classList.toggle('active', item === button));
    document.querySelectorAll('[data-panel]').forEach((panel) => panel.classList.toggle('active', panel.dataset.panel === button.dataset.tab));
  }));

  const matrixNode = document.querySelector('#variantMatrix');
  const variants = matrixNode ? JSON.parse(matrixNode.textContent) : [];
  const selected = {};
  document.querySelectorAll('.choice-group[data-attribute]').forEach((group) => {
    const active = group.querySelector('[data-option-value].selected');
    if (active) selected[group.dataset.attribute] = active.dataset.optionValue;
  });
  const variantId = document.querySelector('#selectedVariantId');
  const price = document.querySelector('#variantPrice');
  const original = document.querySelector('#variantOriginal');
  const saving = document.querySelector('#savingPill');
  const stockStatus = document.querySelector('#stockStatus');
  const quantity = document.querySelector('#purchaseQuantity');
  const actionButtons = document.querySelectorAll('.buy-now, .add-cart');

  function optionHasAvailableVariant(option, attribute) {
    return variants.some((variant) => Number(variant.stock) > 0 && variant.options[attribute] === option);
  }
  function syncVariant() {
    const exact = variants.find((variant) => Object.entries(selected).every(([name, value]) => variant.options[name] === value) && Object.keys(variant.options).length === Object.keys(selected).length);
    const stock = exact ? Number(exact.stock) : 0;
    if (variantId) variantId.value = exact ? exact.id : '';
    if (price) price.textContent = exact ? exact.price : 'Vui lòng chọn phiên bản';
    if (original) original.textContent = exact ? exact.originalPrice : '';
    if (saving) saving.textContent = exact && Number(exact.discount) ? `Tiết kiệm ${exact.discount}%` : '';
    if (stockStatus) {
      stockStatus.className = stock ? 'in-stock' : 'out-stock';
      stockStatus.textContent = stock ? `● Còn hàng (${stock} sản phẩm)` : '● Hết hàng';
    }
    if (quantity) { quantity.max = String(stock || 1); quantity.value = String(Math.min(Math.max(1, Number(quantity.value || 1)), stock || 1)); }
    actionButtons.forEach((button) => { button.disabled = !exact || stock < 1; });
    document.querySelectorAll('.choice-group[data-attribute]').forEach((group) => group.querySelectorAll('[data-option-value]').forEach((button) => {
      button.disabled = !optionHasAvailableVariant(button.dataset.optionValue, group.dataset.attribute);
    }));
  }
  document.querySelectorAll('.choice-group[data-attribute] [data-option-value]').forEach((button) => button.addEventListener('click', () => {
    const group = button.closest('.choice-group');
    if (button.disabled || !group) return;
    selected[group.dataset.attribute] = button.dataset.optionValue;
    group.querySelectorAll('[data-option-value]').forEach((item) => item.classList.toggle('selected', item === button));
    syncVariant();
  }));
  syncVariant();

  document.querySelectorAll('[data-quantity]').forEach((button) => button.addEventListener('click', () => {
    if (!quantity) return;
    const current = Number(quantity.value || 1); const max = Number(quantity.max || 1);
    quantity.value = String(button.dataset.quantity === 'increase' ? Math.min(max, current + 1) : Math.max(1, current - 1));
  }));
  if (quantity) quantity.addEventListener('change', () => { quantity.value = String(Math.min(Number(quantity.max || 1), Math.max(1, Number(quantity.value || 1)))); });

  document.querySelectorAll('.copy-voucher').forEach((button) => button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.dataset.code); button.textContent = 'Đã chép'; setTimeout(() => { button.textContent = 'Sao chép'; }, 1500); } catch (_) { button.textContent = button.dataset.code; }
  }));
  const modal = document.querySelector('#reviewModal');
  const closeModal = () => { if (modal) modal.hidden = true; };
  document.querySelector('#openReviewModal')?.addEventListener('click', () => { if (modal) modal.hidden = false; });
  document.querySelectorAll('[data-close-modal]').forEach((button) => button.addEventListener('click', closeModal));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeModal(); });
});
