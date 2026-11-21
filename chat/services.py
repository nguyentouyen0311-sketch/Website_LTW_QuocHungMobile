import re
import unicodedata

from django.db import transaction
from django.utils import timezone

from .models import ChatConversation, ChatFAQ, ChatMessage

Status = ChatConversation.Status
Sender = ChatMessage.SenderType

MAX_MESSAGE_LENGTH = 1000
MIN_KEYWORD_LENGTH = 2

ESCALATE_NOTICE = (
    'Câu hỏi của bạn đã được ghi nhận và chuyển cho nhân viên bán hàng. '
    'Nhân viên sẽ trả lời tại đây khi sẵn sàng.'
)


class ChatError(Exception):
    pass


CASE_SPECIFIC_PHRASES = [
    'doi so dien thoai',
    'thay doi so dien thoai',
    'sua so dien thoai',
    'cap nhat so dien thoai',
    'doi dia chi',
    'thay doi dia chi',
    'sua dia chi',
    'cap nhat dia chi',
    'doi thong tin giao hang',
    'thay doi thong tin giao hang',
    'doi thong tin nhan hang',
    'thay doi thong tin nhan hang',
    'doi nguoi nhan',
    'thay doi nguoi nhan',
    'doi gio giao',
    'hen gio giao',
    'nhung don van chua',
    'sao don chua',
    'don van chua duoc',
    'don chua duoc xac nhan',
    'chua thay xac nhan',
    'sao chua xac nhan',
    'da chuyen khoan nhung',
    'da thanh toan nhung',
    'chua thay giao hang',
    'sao lau giao',
    'kiem tra giup don',
    'kiem tra don hang giup',
    'tra cuu don hang cua toi',
    'tinh trang don hang cua toi',
]


def normalize_text(text):
    text = (text or '').replace('đ', 'd').replace('Đ', 'D')
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return ' '.join(re.sub(r'[^\w\s]', ' ', text.casefold()).split())


def split_keywords(raw):
    result = []
    for part in (raw or '').replace('\n', ',').split(','):
        keyword = normalize_text(part)
        if len(keyword) >= MIN_KEYWORD_LENGTH:
            result.append(keyword)
    return result


def keyword_matches_text(keyword, normalized_text):
    if not keyword or not normalized_text:
        return False
    pattern = r'\b' + re.escape(keyword) + r'\b'
    return bool(re.search(pattern, normalized_text))


def is_case_specific_request(normalized_text):
    return any(keyword_matches_text(phrase, normalized_text) for phrase in CASE_SPECIFIC_PHRASES)


def find_matching_faq(text):
    from .engine import resolve_question
    result = resolve_question(text)
    return result.get('faq') if result['kind'] == 'faq' else None


def get_guest_key(request, create=False):
    key = request.session.session_key
    if not key and create:
        request.session.create()
        key = request.session.session_key
    return key


def get_open_conversation(request):
    user = request.user
    if user.is_authenticated:
        queryset = ChatConversation.objects.filter(customer=user)
    else:
        key = get_guest_key(request)
        if not key:
            return None
        queryset = ChatConversation.objects.filter(customer__isnull=True, guest_session=key)
    return queryset.exclude(status=Status.CLOSED).order_by('-started_at', '-id').first()


def start_conversation(request):
    user = request.user
    if user.is_authenticated:
        return ChatConversation.objects.create(customer=user)
    return ChatConversation.objects.create(guest_session=get_guest_key(request, create=True))


def get_or_start_conversation(request):
    return get_open_conversation(request) or start_conversation(request)


def clean_content(content):
    content = (content or '').strip()
    if not content:
        raise ChatError('Vui lòng nhập nội dung tin nhắn.')
    if len(content) > MAX_MESSAGE_LENGTH:
        raise ChatError(f'Tin nhắn tối đa {MAX_MESSAGE_LENGTH} ký tự.')
    return content


def _add_message(conversation, sender_type, content, sender_user=None, faq=None, payload=None):
    return ChatMessage.objects.create(
        conversation=conversation, sender_type=sender_type,
        sender_user=sender_user, faq=faq, content=content, payload=payload or {},
    )


def customer_ask_faq(conversation, faq):
    if not faq.is_active:
        raise ChatError('Câu hỏi này hiện không còn được sử dụng.')
    with transaction.atomic():
        conversation = ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.status != Status.BOT:
            raise ChatError('Cuộc trò chuyện đang chờ hoặc do nhân viên hỗ trợ, vui lòng nhắn trực tiếp.')
        _add_message(conversation, Sender.CUSTOMER, faq.question, sender_user=conversation.customer)
        faq.refresh_from_db()
        if not faq.is_active:
            raise ChatError('Nội dung này đã ngừng công bố, vui lòng chọn chủ đề khác.')
        _add_message(conversation, Sender.BOT, faq.answer, faq=faq, payload={'source': faq.category})


def customer_send_text(conversation, content):
    from .engine import resolve_question
    content = clean_content(content)
    with transaction.atomic():
        conversation = ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.status == Status.CLOSED:
            raise ChatError('Cuộc trò chuyện này đã kết thúc.')
        _add_message(conversation, Sender.CUSTOMER, content, sender_user=conversation.customer)
        if conversation.status != Status.BOT:
            return
        result = resolve_question(content)
        if result['kind'] != 'handoff':
            _add_message(conversation, Sender.BOT, result['answer'], faq=result.get('faq'), payload=result.get('payload'))
            return
        changed = ChatConversation.objects.filter(
            pk=conversation.pk, status=Status.BOT,
        ).update(status=Status.WAITING_STAFF)
        if changed:
            _add_message(conversation, Sender.BOT, ESCALATE_NOTICE)


def staff_reply(conversation_id, staff_user, content):
    from accounts.permissions import is_sales_staff
    if not is_sales_staff(staff_user):
        raise ChatError('Bạn không có quyền hỗ trợ hội thoại.')
    content = clean_content(content)
    with transaction.atomic():
        conversation = ChatConversation.objects.select_for_update().get(pk=conversation_id)
        if conversation.status == Status.WAITING_STAFF:
            conversation.status = Status.STAFF
            conversation.assigned_staff = staff_user
            conversation.save(update_fields=['status', 'assigned_staff'])
        if conversation.status != Status.STAFF:
            raise ChatError('Chỉ trả lời được hội thoại đang chờ nhân viên hoặc đang được hỗ trợ.')
        if conversation.assigned_staff_id != staff_user.pk:
            raise ChatError('Hội thoại đang được nhân viên khác hỗ trợ.')
        return _add_message(conversation, Sender.STAFF, content, sender_user=staff_user)


def request_staff(conversation):
    with transaction.atomic():
        conversation = ChatConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.status == Status.CLOSED:
            raise ChatError('Cuộc trò chuyện đã kết thúc.')
        if conversation.status == Status.BOT:
            conversation.status = Status.WAITING_STAFF
            conversation.save(update_fields=['status'])
            _add_message(conversation, Sender.BOT, ESCALATE_NOTICE)


def close_conversation(conversation_id, actor=None):
    from accounts.permissions import is_admin, is_sales_staff
    with transaction.atomic():
        conversation = ChatConversation.objects.select_for_update().get(pk=conversation_id)
        if actor is not None:
            if not is_sales_staff(actor) or (conversation.assigned_staff_id not in (None, actor.pk) and not is_admin(actor)):
                raise ChatError('Chỉ nhân viên phụ trách hoặc admin được kết thúc hội thoại này.')
        if conversation.status == Status.CLOSED:
            return 0
        conversation.status = Status.CLOSED
        conversation.closed_at = timezone.now()
        conversation.save(update_fields=['status', 'closed_at'])
        return 1
