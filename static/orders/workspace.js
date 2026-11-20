document.querySelectorAll('[data-open-support]').forEach(b => b.addEventListener('click', () =>
  document.getElementById('support-dialog')?.showModal()));
document.querySelectorAll('[data-open-policy]').forEach(b => b.addEventListener('click', e => {
  e.preventDefault();
  document.getElementById('order-policy')?.showModal()
}));
document.querySelectorAll('[data-close-dialog]').forEach(b => b.addEventListener('click', () => b
  .closest('dialog').close()));
document.querySelector('[data-toggle-menu]')?.addEventListener('click', () => document
  .querySelector('.admin-sidebar').classList.toggle('open'));
document.querySelector('[data-toggle-cancel]')?.addEventListener('click', () => {
  const d = document.getElementById('staff-cancel');
  d.open = !d.open
});
document.querySelectorAll('[data-row-link]').forEach(row => row.addEventListener('click', e => {
  if (!e.target.closest('a,button,input')) location.href = row.dataset.rowLink
}));
const checkout = document.getElementById('checkout-form');
if (checkout) {
  const syncShipping = () => {
    const pickup = checkout.elements.shipping_method.value === 'PICKUP';
    checkout.querySelectorAll('[data-delivery-address]').forEach(el => {
      el.hidden = pickup
    });
  };
  checkout.querySelectorAll('[name=shipping_method]').forEach(i => i.addEventListener('change',
    syncShipping));
  syncShipping();
  document.querySelector('[data-default-address]')?.addEventListener('click', () => {
    const d = JSON.parse(document.getElementById('default-recipient').textContent);
    for (const key of ['recipient_name', 'recipient_phone', 'shipping_address']) checkout
      .elements[key].value = d[key] || '';
  });
}
const returnForm = document.getElementById('return-form');
if (returnForm) {
  const formatter = new Intl.NumberFormat('vi-VN', {
    style: 'currency',
    currency: 'VND',
    maximumFractionDigits: 2
  });
  const syncReturn = () => {
    const type = returnForm.querySelector('[name=request_type]:checked');
    let total = 0;
    returnForm.querySelectorAll('[name=items]:checked').forEach(i => {
      total += Number(i.dataset.amount.replace(',', '.'))
    });
    document.getElementById('return-item-amount').textContent = formatter.format(total);
    document.getElementById('return-expected-amount').textContent = formatter.format(type
      ?.value === 'CANCEL' ? 0 : total);
    document.getElementById('return-type-label').textContent = type?.closest('label')
      .querySelector('strong').textContent || 'Chưa chọn';
  };
  returnForm.querySelectorAll('[name=request_type],[name=items]').forEach(i => i.addEventListener(
    'change', syncReturn));
  syncReturn();
  const input = document.getElementById('id_attachments'),
    previews = document.getElementById('attachment-previews'),
    status = document.getElementById('upload-status');
  let files = [],
    urls = [];
  const renderFiles = () => {
    urls.forEach(u => URL.revokeObjectURL(u));
    urls = [];
    previews.replaceChildren();
    const transfer = new DataTransfer();
    files.forEach((f, index) => {
      transfer.items.add(f);
      const wrapper = document.createElement('div');
      wrapper.className = 'upload-preview';
      const media = document.createElement(f.type.startsWith('video/') ? 'video' : 'img');
      const url = URL.createObjectURL(f);
      urls.push(url);
      media.src = url;
      if (media.tagName === 'IMG') media.alt = f.name;
      else media.controls = true;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '×';
      button.setAttribute('aria-label', 'Xóa ' + f.name);
      button.addEventListener('click', () => {
        files.splice(index, 1);
        renderFiles()
      });
      wrapper.append(media, button);
      previews.append(wrapper)
    });
    input.files = transfer.files;
    status.textContent = 'Đã tải ' + files.length + '/5 tệp';
    input.setCustomValidity('');
  };
  input.addEventListener('change', () => {
    const combined = [...files, ...input.files];
    if (combined.length > 5) {
      renderFiles();
      status.textContent = 'Chỉ được chọn tối đa 5 tệp.';
      return
    }
    if (combined.some(f => !['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm']
        .includes(f.type) || f.size > (f.type.startsWith('video/') ? 50 : 10) * 1024 * 1024)) {
      renderFiles();
      status.textContent = 'Tệp không đúng định dạng hoặc vượt dung lượng cho phép.';
      return
    }
    files = combined;
    renderFiles()
  });
}
