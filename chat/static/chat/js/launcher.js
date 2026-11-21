(() => {
  'use strict';
  const trigger = document.querySelector('.qhm-chat-launcher');
  if (!trigger || trigger.dataset.ready) return;
  trigger.dataset.ready = 'true';
  let panel, frame, close;
  function hide() {
    if (panel) panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  }
  trigger.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!panel) {
      panel = document.createElement('section');
      panel.className = 'qhm-chat-widget';
      panel.id = 'qhm-chat-widget';
      panel.setAttribute('role', 'dialog');
      panel.setAttribute('aria-label', 'Trợ lý Quốc Hùng Mobile');
      close = document.createElement('button');
      close.type = 'button'; close.className = 'qhm-chat-widget-close'; close.textContent = '×';
      close.setAttribute('aria-label', 'Thu nhỏ cửa sổ chat'); close.addEventListener('click', hide);
      frame = document.createElement('iframe');
      const url = new URL(trigger.href);
      url.searchParams.set('embed', '1');
      frame.src = url;
      frame.title = 'Hội thoại hỗ trợ Quốc Hùng Mobile';
      frame.addEventListener('load', () => { try {frame.contentDocument.addEventListener('keydown', event => {if (event.key === 'Escape') hide();});} catch {} });
      panel.append(close, frame); document.body.append(panel);
      trigger.setAttribute('aria-controls', panel.id);
    }
    panel.hidden = !panel.hidden && trigger.getAttribute('aria-expanded') === 'true';
    trigger.setAttribute('aria-expanded', String(!panel.hidden));
    if (!panel.hidden) close.focus(); else trigger.focus();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && panel && !panel.hidden) hide(); });
})();
