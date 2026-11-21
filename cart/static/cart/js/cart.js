document.addEventListener('DOMContentLoaded', () => {
  const page = document.querySelector('.qhm-cart-page');
  if (!page) return;
  const selectAll = page.querySelector('#cart-select-all');
  const bulkForm = page.querySelector('#cart-remove-selected');
  const selectedInputs = [...page.querySelectorAll('input[name="item_ids"]')];
  const allControl = page.querySelector('[data-select-all-control]');
  if (allControl) allControl.hidden = false;
  const synchronizeSelection = () => {
    if (!selectAll || !bulkForm) return;
    const count = selectedInputs.filter(input => input.checked).length;
    selectAll.checked = count === selectedInputs.length && count > 0;
    selectAll.indeterminate = count > 0 && count < selectedInputs.length;
    bulkForm.querySelector('button[type="submit"]').disabled = count === 0;
  };
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      selectedInputs.forEach(input => { input.checked = selectAll.checked; });
      synchronizeSelection();
    });
  }
  selectedInputs.forEach(input => input.addEventListener('change', synchronizeSelection));
  synchronizeSelection();
  page.querySelectorAll('[data-step]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => {
      const form = button.closest('form');
      if (form.dataset.submitting) return;
      const input = form.querySelector('[name="quantity"]');
      const current = Number(input.value);
      const next = current + Number(button.dataset.step);
      const maximum = Number(input.dataset.max);
      if (!Number.isSafeInteger(current) || next < 1 || next > maximum) return;
      input.value = String(next);
      form.requestSubmit();
    });
  });
  page.querySelectorAll('.cart-variant-form select').forEach(select => {
    select.addEventListener('change', () => {
      if (!select.form.dataset.submitting) select.form.requestSubmit();
    });
  });
  page.querySelectorAll('form[method="post"]').forEach(form => {
    form.addEventListener('submit', event => {
      if (form.dataset.submitting) {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = 'true';
      form.setAttribute('aria-busy', 'true');
      form.querySelectorAll('button[type="submit"], [data-step]').forEach(button => {
        button.disabled = true;
      });
    });
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) window.location.reload();
  });
});
