const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn, spawnSync} = require('node:child_process');
const project = path.resolve(__dirname, '..', '..');
const review = process.env.CHAT_REVIEW_ROOT || fs.mkdtempSync(path.join(os.tmpdir(), 'qhm-chat-review-'));
fs.mkdirSync(review, {recursive: true});
if (fs.existsSync(path.join(review, 'chat-review.sqlite3'))) throw new Error('CHAT_REVIEW_ROOT phải là thư mục kiểm thử mới, chưa có chat-review.sqlite3.');
const python = path.join(project, '.venv', 'Scripts', 'python.exe');
const environment = {...process.env, PYTHONPATH: review, CHAT_PROJECT_ROOT: project, DJANGO_SETTINGS_MODULE: 'chat_review_settings'};
fs.writeFileSync(path.join(review, 'chat_review_settings.py'), `from pathlib import Path
from config.settings import *
REVIEW_ROOT = Path(__file__).resolve().parent
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': REVIEW_ROOT / 'chat-review.sqlite3'}}
MEDIA_ROOT = REVIEW_ROOT / 'chat-review-media'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
`);
fs.writeFileSync(path.join(review, 'chat_review_seed.py'), `import json, os, sys
sys.path.insert(0, os.environ['CHAT_PROJECT_ROOT'])
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.contrib.auth import get_user_model
from django.test import Client
from catalog.models import Category, Product, ProductVariant, Attribute, AttributeValue, VariantAttributeValue
from chat.models import ChatFAQ
call_command('migrate', 'chat', '0001', verbosity=0, interactive=False)
with connection.cursor() as cursor:
    for title in ['Legacy draft one', 'Legacy draft two']:
        cursor.execute('INSERT INTO chat_chatfaq (question, answer, keywords, is_active) VALUES (%s, %s, %s, %s)', [title, 'Preserved legacy answer', 'legacy draft', False])
call_command('migrate', verbosity=0, interactive=False)
legacy = ChatFAQ.objects.filter(question__startswith='Legacy draft')
assert legacy.count() == 2 and len({str(item.knowledge_id) for item in legacy}) == 2
call_command('sync_chat_knowledge', verbosity=0)
admin, unused = get_user_model().objects.get_or_create(username='chat_review_admin', defaults={'is_superuser': True, 'is_staff': True})
category, unused = Category.objects.get_or_create(name='Điện thoại kiểm thử')
product, unused = Product.objects.get_or_create(name='iPhone 16', defaults={'category': category, 'brand': 'Apple', 'description': 'Màn hình OLED · Dữ liệu kiểm thử riêng'})
attribute, unused = Attribute.objects.get_or_create(name='Phiên bản kiểm thử')
for capacity, price in [('256GB', 24000000), ('512GB', 26000000)]:
    value, unused = AttributeValue.objects.get_or_create(attribute=attribute, value=capacity)
    variant, unused = ProductVariant.objects.get_or_create(sku='CHAT-' + capacity, defaults={'product': product, 'price': price, 'combination_key': capacity})
    VariantAttributeValue.objects.get_or_create(variant=variant, attribute=attribute, defaults={'attribute_value': value})
client = Client()
client.force_login(admin)
print(json.dumps({'admin_session': client.cookies['sessionid'].value, 'legacy_preserved': True}))
`);
const seed = spawnSync(python, ['-X', 'utf8', path.join(review, 'chat_review_seed.py')], {cwd: project, env: environment, windowsHide: true, encoding: 'utf8'});
if (seed.status !== 0) throw new Error(seed.stdout + seed.stderr);
const fixture = JSON.parse(seed.stdout.trim().split(/\r?\n/).at(-1));
const server = spawn(python, ['-X', 'utf8', '-u', 'manage.py', 'runserver', '127.0.0.1:8016', '--noreload'], {cwd: project, env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
let serverOutput = '', closing = false;
server.stdout.on('data', value => serverOutput += value);
server.stderr.on('data', value => serverOutput += value);
server.on('exit', code => { if (code && !closing) console.error('Django exited: ' + code + '\n' + serverOutput); });
const chrome = spawn(process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', '--user-data-dir=' + path.join(review, 'chat-chrome'), 'about:blank'], {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
let socket, sequence = 0, sessionId;
const pending = new Map(), checks = [], errors = [], layouts = [];
function command(method, params = {}, session = sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Timeout: ' + method)); }, 15000);
    pending.set(id, {resolve, reject, timer});
    socket.send(JSON.stringify({id, method, params, ...(session ? {sessionId: session} : {})}));
  });
}
async function evaluate(expression) {
  const result = await command('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function waitFor(expression) {
  for (let index = 0; index < 160; index++) {
    try { if (await evaluate(expression)) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Condition failed: ' + expression);
}
function check(name, value) { checks.push({name, passed: !!value}); if (!value) throw new Error(name); }
async function click(selector) {
  const point = await evaluate(`(() => {const element = document.querySelector(${JSON.stringify(selector)}); element.scrollIntoView({block: 'center'}); const box = element.getBoundingClientRect(); return {x: box.x + box.width / 2, y: box.y + box.height / 2};})()`);
  await command('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', clickCount: 1});
  await command('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', clickCount: 1});
}
async function fill(selector, value) { await evaluate(`(() => {const element = document.querySelector(${JSON.stringify(selector)}); element.value = ${JSON.stringify(value)}; element.dispatchEvent(new Event('input', {bubbles: true}));})()`); }
async function navigate(route, selector) { await command('Page.navigate', {url: 'http://127.0.0.1:8016' + route}); await waitFor(`document.readyState === 'complete' && !!document.querySelector(${JSON.stringify(selector)})`); }
async function screenshot(name) { const data = await command('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true}); fs.writeFileSync(path.join(review, name + '.png'), Buffer.from(data.data, 'base64')); }
async function newTab(context) { const {targetId} = await command('Target.createTarget', {url: 'about:blank', ...(context ? {browserContextId: context} : {})}, null); const result = await command('Target.attachToTarget', {targetId, flatten: true}, null); sessionId = result.sessionId; await command('Page.enable'); await command('Runtime.enable'); await command('Network.enable'); return sessionId; }
async function send(text, expected) { await fill('form[data-draft-key] textarea', text); await click('form[data-draft-key] button'); await waitFor(expected); }
async function main() {
  const address = await new Promise((resolve, reject) => { let output = ''; const timeout = setTimeout(() => reject(new Error('Chrome launch timeout')), 15000); chrome.stderr.on('data', data => { output += data; const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (match) {clearTimeout(timeout); resolve(match[1]);} }); chrome.on('error', reject); });
  socket = new WebSocket(address);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); });
  socket.addEventListener('message', event => { const message = JSON.parse(event.data); if (message.id) { const item = pending.get(message.id); if (item) {clearTimeout(item.timer); pending.delete(message.id); message.error ? item.reject(new Error(JSON.stringify(message.error))) : item.resolve(message.result);} } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails); });
  for (let index = 0; index < 100 && !serverOutput.includes('Starting WSGI development server'); index++) await new Promise(resolve => setTimeout(resolve, 100));
  if (!serverOutput.includes('Starting WSGI development server')) throw new Error(serverOutput);
  const customer = await newTab();
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await navigate('/chat/', '.chat-customer');
  check('Customer workspace renders', await evaluate("document.body.innerText.includes('Trợ lý Quốc Hùng Mobile')"));
  await send('Có COD không?', "document.querySelectorAll('[data-message-id]').length === 2");
  check('COD trigger answers automatically', await evaluate("document.querySelector('[data-chat-thread]').innerText.includes('COD là thanh toán khi nhận hàng')"));
  await send('Giá iPhone 16', "document.querySelectorAll('.chat-product').length > 0");
  check('Actual variant prices are displayed', await evaluate("document.querySelector('[data-chat-thread]').innerText.includes('24.000.000đ') && document.querySelector('[data-chat-thread]').innerText.includes('26.000.000đ')"));
  await send('COD và bảo hành', "document.querySelectorAll('[data-chat-thread] .chat-choices button').length === 2");
  const warrantyAction = await evaluate("[...document.querySelectorAll('[data-chat-thread] .chat-choices button')].find(button => button.innerText.includes('bảo hành')).form.getAttribute('action')");
  await click(`[data-chat-thread] form[action="${warrantyAction}"] button`);
  await waitFor("document.querySelector('[data-chat-thread]').innerText.includes('chưa xác nhận thời hạn')");
  check('Ambiguous topic choice works', true);
  for (const width of [1280, 1024, 768, 375, 320]) {
    await command('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: false});
    await new Promise(resolve => setTimeout(resolve, 300));
    const geometry = await evaluate('({width: innerWidth, scroll: document.documentElement.scrollWidth})');
    layouts.push({screen: 'customer', ...geometry}); check('Customer has no overflow at ' + width, geometry.scroll <= width + 1);
    if ([1280, 375].includes(width)) await screenshot('chat-customer-' + width);
  }
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await click('[data-handoff-button]');
  await waitFor("document.querySelector('[data-status-label]').innerText.includes('Chờ nhân viên')");
  const conversation = await evaluate("document.querySelector('[data-chat-live]').dataset.conversation");
  const context = await command('Target.createBrowserContext', {}, null);
  const staff = await newTab(context.browserContextId);
  await command('Network.setCookie', {name: 'sessionid', value: fixture.admin_session, url: 'http://127.0.0.1:8016', httpOnly: true, sameSite: 'Lax'});
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await navigate('/chat/staff/', '.chat-staff-grid');
  await click(`a.chat-queue-item[href*="/staff/${conversation}/"]`);
  await waitFor("!!document.querySelector('form[data-draft-key]')");
  await send('Nhân viên đã tiếp nhận yêu cầu của bạn.', "document.querySelector('[data-chat-thread]').innerText.includes('Nhân viên đã tiếp nhận')");
  sessionId = customer;
  await waitFor("document.querySelector('[data-chat-thread]').innerText.includes('Nhân viên đã tiếp nhận')");
  check('Staff response appears on customer page without reload', true);
  sessionId = staff;
  for (const width of [1280, 1024, 768, 375, 320]) {
    await command('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: false});
    await new Promise(resolve => setTimeout(resolve, 300));
    const geometry = await evaluate('({width: innerWidth, scroll: document.documentElement.scrollWidth, sidebar: document.querySelector(".qhm-sidebar").getBoundingClientRect().width})');
    layouts.push({screen: 'staff', ...geometry}); check('Staff has no overflow at ' + width, geometry.scroll <= width + 1);
    if ([1280, 375].includes(width)) await screenshot('chat-staff-' + width);
  }
  await click('#qhm-menu-btn');
  check('Mobile sidebar opens', await evaluate("document.getElementById('qhm-sidebar').classList.contains('is-open')"));
  await command('Input.dispatchKeyEvent', {type: 'keyDown', key: 'Escape', code: 'Escape'});
  check('Escape closes mobile sidebar', await evaluate("!document.getElementById('qhm-sidebar').classList.contains('is-open')"));
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await click('.chat-tabs a[href="/chat/knowledge/"]');
  await waitFor("!!document.querySelector('.chat-knowledge-panel')");
  check('Knowledge tab navigation works', true);
  await click('a[href="/chat/knowledge/new/"]');
  await waitFor("!!document.querySelector('#id_question')");
  await fill('#id_question', 'Chính sách thử nghiệm qua trình duyệt'); await fill('#id_category', 'Kiểm thử'); await fill('#id_answer', 'Nội dung đã được admin kiểm tra.'); await fill('#id_keywords', 'chính sách thử nghiệm'); await click('#id_is_active'); await click('.chat-editor form button');
  await waitFor("!!document.querySelector('.chat-knowledge-panel') && document.body.innerText.includes('Chính sách thử nghiệm qua trình duyệt')");
  check('Admin creates published knowledge', true);
  await click('.chat-tabs a[href="/chat/knowledge/test/"]');
  await waitFor("!!document.querySelector('#id_content')");
  await fill('#id_content', 'chinh sach thu nghiem'); await click('.chat-editor form button');
  await waitFor("document.body.innerText.includes('Nội dung đã được admin kiểm tra.')");
  check('Admin test tool matches unaccented question', true);
  const uploadPath = path.join(review, 'chat-import-browser.json');
  fs.writeFileSync(uploadPath, JSON.stringify({version: 1, entries: [{knowledge_id: '80000000-0000-4000-8000-000000000001', question: 'Nội dung nhập từ trình duyệt', category: 'Kiểm thử', answer: 'Nhập tệp thành công.', keywords: 'nhập tệp thử nghiệm', priority: 0, is_active: false}]}));
  await navigate('/chat/knowledge/import/', '#id_file');
  const document = await command('DOM.getDocument'); const input = await command('DOM.querySelector', {nodeId: document.root.nodeId, selector: '#id_file'});
  await command('DOM.setFileInputFiles', {nodeId: input.nodeId, files: [uploadPath]}); await click('.chat-editor form button');
  await waitFor("!!document.querySelector('[name=token]')");
  check('Import previews before writing', await evaluate("document.body.innerText.includes('Nội dung nhập từ trình duyệt')"));
  await click('form:has([name=token]) button');
  await waitFor("location.pathname === '/chat/knowledge/' && document.readyState === 'complete' && !!document.querySelector('.chat-knowledge-panel') && document.body.innerText.includes('Nội dung nhập từ trình duyệt')");
  check('Import confirmation persists knowledge', true);
  for (const width of [1280, 375, 320]) {
    await command('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: false});
    check('Knowledge table stays inside page at ' + width, await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'));
    if (width === 1280) await screenshot('chat-knowledge-1280');
  }
  sessionId = customer;
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await navigate('/chat/', '.chat-customer');
  await click('form[action="/chat/close/"] button');
  await waitFor("document.querySelector('[data-status-label]').innerText.includes('Đã kết thúc')");
  await navigate('/cart/', '.qhm-chat-launcher');
  await click('.qhm-chat-launcher');
  await waitFor("!!document.querySelector('.qhm-chat-widget iframe')?.contentDocument?.querySelector('.chat-embed')");
  check('Floating chat opens inside cart', true);
  check('Chat header follows forest palette', await evaluate("getComputedStyle(document.querySelector('.qhm-chat-widget iframe').contentDocument.querySelector('.chat-conversation-head')).backgroundColor === 'rgb(31, 42, 36)'"));
  await evaluate("(() => {const inner = document.querySelector('.qhm-chat-widget iframe').contentDocument; inner.querySelector('textarea').value = 'Có COD không?'; inner.querySelector('form[data-draft-key]').requestSubmit();})()");
  await waitFor("document.querySelector('.qhm-chat-widget iframe')?.contentDocument?.querySelectorAll('[data-message-id]').length === 2");
  check('Embedded chat sends and answers without leaving cart', await evaluate("location.pathname === '/cart/' && document.querySelector('.qhm-chat-widget iframe').contentWindow.location.search.includes('embed=1')"));
  await evaluate("(() => {const inner = document.querySelector('.qhm-chat-widget iframe').contentDocument; const input = inner.querySelector('textarea'); input.value = 'Bản nháp cần giữ'; input.dispatchEvent(new Event('input', {bubbles: true}));})()");
  await click('.qhm-chat-widget-close');
  check('Floating chat minimizes', await evaluate("document.querySelector('.qhm-chat-widget').hidden"));
  await click('.qhm-chat-launcher');
  check('Reopening preserves draft and conversation', await evaluate("document.querySelector('.qhm-chat-widget iframe').contentDocument.querySelector('textarea').value === 'Bản nháp cần giữ'"));
  await evaluate("(() => {const frame = document.querySelector('.qhm-chat-widget iframe'); const inner = frame.contentDocument; const key = 'qhm-chat-draft-' + inner.querySelector('form[data-draft-key]').dataset.draftKey; const lastId = Math.max(...[...inner.querySelectorAll('[data-message-id]')].map(item => Number(item.dataset.messageId))); frame.contentWindow.sessionStorage.setItem('qhm-chat-pending', JSON.stringify({key, lastId})); frame.contentWindow.location.reload();})()");
  await waitFor("document.querySelector('.qhm-chat-widget iframe')?.contentDocument?.querySelector('textarea')?.value === 'Bản nháp cần giữ'");
  check('Unconfirmed send preserves draft after reload', true);
  await command('Emulation.setDeviceMetricsOverride', {width: 320, height: 740, deviceScaleFactor: 1, mobile: false});
  await new Promise(resolve => setTimeout(resolve, 300));
  const widgetGeometry = await evaluate("(() => {const box = document.querySelector('.qhm-chat-widget').getBoundingClientRect(); return {width: innerWidth, height: innerHeight, left: box.left, right: box.right, bottom: box.bottom};})()");
  layouts.push({screen: 'widget', ...widgetGeometry});
  check('Floating chat fits narrow screens', widgetGeometry.left >= -1 && widgetGeometry.right <= widgetGeometry.width + 1 && widgetGeometry.bottom <= widgetGeometry.height + 1);
  await screenshot('chat-widget-320');
  await command('Emulation.setDeviceMetricsOverride', {width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false});
  await screenshot('chat-widget-1280');
  check('No JavaScript runtime errors', errors.length === 0);
  check('Legacy migration preserved distinct records', fixture.legacy_preserved);
  fs.writeFileSync(path.join(review, 'chat-browser-review.json'), JSON.stringify({checks, layouts, errors, review}, null, 2));
  console.log(JSON.stringify({passed: checks.length, failed: 0, review}));
}
main().catch(async error => { try {await screenshot('chat-browser-failure');} catch {} fs.writeFileSync(path.join(review, 'chat-browser-review.json'), JSON.stringify({error: error.stack, checks, layouts, errors, serverOutput}, null, 2)); console.error(error.stack); process.exitCode = 1; }).finally(async () => { closing = true; try {if (socket?.readyState === WebSocket.OPEN) await command('Browser.close', {}, null);} catch {} for (const item of [server, chrome]) if (item.pid) spawnSync('taskkill', ['/PID', String(item.pid), '/T', '/F'], {windowsHide: true, stdio: 'ignore', timeout: 5000}); for (const item of pending.values()) clearTimeout(item.timer); socket?.close(); });
