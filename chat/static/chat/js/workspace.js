(() => {
  'use strict';
  const sidebar = document.getElementById('qhm-sidebar');
  const overlay = document.getElementById('qhm-overlay');
  const menu = document.getElementById('qhm-menu-btn') || document.getElementById('qhm-sidebar-toggle');
  const close = document.getElementById('qhm-sidebar-close');
  function setMenu(open) {
    sidebar?.classList.toggle('is-open', open);
    overlay?.classList.toggle('is-open', open);
    menu?.setAttribute('aria-expanded', String(open));
    if (open) close?.focus(); else menu?.focus();
  }
  menu?.addEventListener('click', () => setMenu(true));
  close?.addEventListener('click', () => setMenu(false));
  overlay?.addEventListener('click', () => setMenu(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar?.classList.contains('is-open')) setMenu(false);
    if (event.key === 'Tab' && sidebar?.classList.contains('is-open')) {
      const elements = [...sidebar.querySelectorAll('a[href],button')].filter(item => item.offsetParent !== null);
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  const storage = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch {} },
    remove(key) { try { sessionStorage.removeItem(key); } catch {} }
  };
  try {
    const pendingDraft = JSON.parse(storage.get('qhm-chat-pending') || 'null');
    if (pendingDraft) {
      const text = (storage.get(pendingDraft.key) || '').trim();
      const sender = pendingDraft.key.includes('draft-staff-') ? 'STAFF' : 'CUSTOMER';
      const persisted = [...document.querySelectorAll('[data-message-id]')].some(item => Number(item.dataset.messageId) > pendingDraft.lastId && item.dataset.sender === sender && item.dataset.content === text);
      if (persisted) storage.remove(pendingDraft.key);
    }
  } catch {}
  storage.remove('qhm-chat-pending');
  document.querySelectorAll('form[data-draft-key]').forEach(form => {
    const textarea = form.querySelector('textarea');
    if (!textarea) return;
    const key = 'qhm-chat-draft-' + form.dataset.draftKey;
    if (!textarea.value) textarea.value = storage.get(key) || '';
    textarea.addEventListener('input', () => storage.set(key, textarea.value));
    form.addEventListener('submit', () => {
      storage.set(key, textarea.value);
      const lastId = Math.max(0, ...[...document.querySelectorAll('[data-message-id]')].map(item => Number(item.dataset.messageId)));
      storage.set('qhm-chat-pending', JSON.stringify({key, lastId}));
    });
  });
  document.querySelectorAll('[data-chat-live]').forEach(root => {
    const thread = root.querySelector('[data-chat-thread]');
    const connection = root.querySelector('[data-connection-status]');
    const refresh = root.querySelector('[data-chat-refresh]');
    let signature = null, timer, busy = false;
    if (thread) thread.scrollTop = thread.scrollHeight;
    async function update() {
      clearTimeout(timer);
      if (busy) return;
      if (document.hidden) { timer = setTimeout(update, 4000); return; }
      busy = true;
      if (refresh) refresh.disabled = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12000);
      let delay = 4000;
      try {
        const url = new URL(root.dataset.snapshotUrl, location.origin);
        if (root.dataset.conversation) url.searchParams.set('conversation', root.dataset.conversation);
        if (root.dataset.embedded) url.searchParams.set('embed', '1');
        const query = new URLSearchParams(location.search);
        for (const key of ['status', 'page']) if (query.has(key)) url.searchParams.set(key, query.get(key));
        const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}, signal: controller.signal});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Không tải được hội thoại');
        const data = await response.json();
        const nextSignature = `${data.conversation_id}:${data.last_id}:${data.status}`;
        if (thread && signature !== nextSignature) {
          const follow = signature === null || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
          thread.innerHTML = data.thread;
          if (follow) thread.scrollTop = thread.scrollHeight;
        }
        signature = nextSignature;
        const queue = root.querySelector('[data-chat-queue]');
        if (queue && data.queue && queue.innerHTML !== data.queue) {
          const focused = queue.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
          queue.innerHTML = data.queue;
          if (focused) [...queue.querySelectorAll('a')].find(link => link.getAttribute('href') === focused)?.focus({preventScroll: true});
        }
        const label = root.querySelector('[data-status-label]');
        if (label) label.textContent = data.status_label;
        const suggestions = root.querySelector('[data-faq-suggestions]');
        if (suggestions) suggestions.hidden = data.status !== 'BOT';
        const handoff = root.querySelector('[data-handoff-button]');
        if (handoff) handoff.disabled = ['WAITING_STAFF', 'STAFF'].includes(data.status);
        const staffSend = root.querySelector('[data-staff-send]');
        if (staffSend) staffSend.disabled = !data.can_reply;
        const availability = root.querySelector('[data-availability]');
        if (availability) availability.textContent = data.can_reply ? 'Bạn có thể trả lời khách hàng.' : 'Hội thoại đã kết thúc, đang do bot hoặc do nhân viên khác hỗ trợ.';
        if (connection) connection.textContent = data.status === 'CLOSED' ? 'Hội thoại đã kết thúc' : 'Đã cập nhật · Tự làm mới';
      } catch {
        delay = 15000;
        if (connection) connection.textContent = 'Chưa kết nối được. Bản nháp được giữ trong tab này; chọn Tải lại để thử lại.';
      } finally {
        clearTimeout(timeout);
        busy = false;
        if (refresh) refresh.disabled = false;
        timer = setTimeout(update, delay);
      }
    }
    refresh?.addEventListener('click', update);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
    timer = setTimeout(update, 1500);
  });
  if (document.querySelector('.chat-customer')) document.querySelector('.cart-header-action[aria-current]')?.removeAttribute('aria-current');
})();
