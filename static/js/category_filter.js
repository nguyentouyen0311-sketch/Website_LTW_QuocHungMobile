document.addEventListener('DOMContentLoaded', () => {
  const brandButtons = document.querySelectorAll('.brand-pill');
  const cards = document.querySelectorAll('.phone-product-card');
  const toast = document.querySelector('.cart-toast');
  let toastTimer;
  brandButtons.forEach(button => button.addEventListener('click', () => {
    brandButtons.forEach(item => item.classList.remove('is-selected'));
    button.classList.add('is-selected');
    const brand = button.dataset.brand;
    cards.forEach(card => { card.hidden = brand !== 'all' && card.dataset.brand !== brand; });
  }));
  document.querySelectorAll('.option-grid button').forEach(button => button.addEventListener('click', () => {
    const group = button.dataset.filter;
    document.querySelectorAll(`[data-filter="${group}"]`).forEach(item => item.classList.remove('is-selected'));
    button.classList.add('is-selected');
  }));
  document.querySelectorAll('.sort-options button').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.sort-options button').forEach(item => item.classList.remove('is-selected'));
    button.classList.add('is-selected');
  }));
  document.querySelectorAll('.active-chips button').forEach(chip => chip.addEventListener('click', () => chip.remove()));
  document.getElementById('clearAllFilters')?.addEventListener('click', () => {
    document.querySelectorAll('.filter-card input[type="checkbox"]').forEach(input => { input.checked = false; });
    document.querySelectorAll('.option-grid button').forEach(button => button.classList.remove('is-selected'));
    document.querySelectorAll('.active-chips button').forEach(chip => chip.remove());
    brandButtons.forEach(item => item.classList.remove('is-selected'));
    document.querySelector('.brand-pill[data-brand="all"]')?.classList.add('is-selected');
    cards.forEach(card => { card.hidden = false; });
  });
  document.querySelectorAll('.add-to-cart').forEach(button => button.addEventListener('click', () => {
    button.textContent = '✓ Đã thêm';
    toast?.classList.add('is-visible');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast?.classList.remove('is-visible'), 2600);
    window.setTimeout(() => { button.innerHTML = '🛒 Thêm vào giỏ'; }, 1400);
  }));
});
