import json
import time
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from accounts.permissions import ROLE_ADMIN, ROLE_SALES_STAFF, is_admin, is_customer, is_sales_staff, role_required
from catalog.models import Category

from . import services
from .engine import resolve_question
from .forms import ChatContentForm, KnowledgeForm, KnowledgeImportForm
from .knowledge import KnowledgeError, export_csv, export_json, import_records, parse_knowledge, preview_records
from .models import ChatConversation, ChatFAQ


Status = ChatConversation.Status


def customer_guard(request):
    if not request.user.is_authenticated or is_customer(request.user):
        return None
    if is_sales_staff(request.user):
        return redirect('chat:staff_inbox')
    raise PermissionDenied('Tài khoản của bạn không sử dụng chức năng chat với khách.')


def own_conversation(request, identifier=None):
    if not identifier:
        return services.get_open_conversation(request)
    if not str(identifier).isdigit():
        raise Http404('Không tìm thấy hội thoại.')
    queryset = ChatConversation.objects.all()
    if request.user.is_authenticated:
        queryset = queryset.filter(customer=request.user)
    else:
        key = services.get_guest_key(request)
        queryset = queryset.filter(customer__isnull=True, guest_session=key) if key else queryset.none()
    return get_object_or_404(queryset, pk=identifier)


def thread_context(conversation, staff=False):
    return {
        'conversation': conversation,
        'chat_messages': list(conversation.messages.select_related('sender_user').order_by('-id')[:200])[::-1] if conversation else [],
        'staff_view': staff,
    }


def customer_page(request, form=None, status=200):
    conversation = own_conversation(request, request.GET.get('conversation'))
    context = thread_context(conversation)
    embedded = request.GET.get('embed') == '1'
    context.update(embedded=embedded, chat_base='chat/embed_base.html' if embedded else 'cart/base.html', form=form if form is not None else ChatContentForm(), faqs=ChatFAQ.objects.filter(is_active=True).order_by('-priority', 'id')[:8], categories=Category.objects.filter(is_visible=True).order_by('name'), show_faqs=conversation is None or conversation.status == Status.BOT)
    return render(request, 'chat/customer_workspace.html', context, status=status)


def customer_redirect(request, conversation=None):
    parameters = []
    if request.GET.get('embed') == '1':
        parameters.append('embed=1')
    if conversation:
        parameters.append(f'conversation={conversation.pk}')
    return redirect(reverse('chat:chat') + ('?' + '&'.join(parameters) if parameters else ''))


@never_cache
@xframe_options_sameorigin
def chat_view(request):
    return customer_guard(request) or customer_page(request)


@require_POST
@xframe_options_sameorigin
def chat_send_view(request):
    blocked = customer_guard(request)
    if blocked:
        return blocked
    form = ChatContentForm(request.POST)
    if form.is_valid():
        try:
            services.customer_send_text(services.get_or_start_conversation(request), form.cleaned_data['content'])
            return customer_redirect(request)
        except services.ChatError as error:
            form.add_error(None, str(error))
    return customer_page(request, form=form, status=400)


@require_POST
def chat_ask_faq_view(request, faq_id):
    blocked = customer_guard(request)
    if blocked:
        return blocked
    faq = get_object_or_404(ChatFAQ, pk=faq_id, is_active=True)
    try:
        services.customer_ask_faq(services.get_or_start_conversation(request), faq)
    except services.ChatError as error:
        messages.error(request, str(error))
    return customer_redirect(request)


@require_POST
def chat_handoff_view(request):
    blocked = customer_guard(request)
    if blocked:
        return blocked
    try:
        services.request_staff(services.get_or_start_conversation(request))
    except services.ChatError as error:
        messages.error(request, str(error))
    return customer_redirect(request)


@require_POST
def chat_close_view(request):
    blocked = customer_guard(request)
    if blocked:
        return blocked
    conversation = services.get_open_conversation(request)
    if conversation:
        services.close_conversation(conversation.pk)
        messages.success(request, 'Đã kết thúc cuộc trò chuyện. Gửi tin nhắn mới để bắt đầu phiên hỗ trợ khác.')
        return customer_redirect(request, conversation)
    return customer_redirect(request)


@never_cache
@require_GET
def customer_snapshot_view(request):
    blocked = customer_guard(request)
    if blocked:
        return blocked
    conversation = own_conversation(request, request.GET.get('conversation'))
    return snapshot_response(request, conversation, False)


def queue_context(request):
    status = request.GET.get('status', Status.WAITING_STAFF)
    if status not in dict(Status.choices):
        status = Status.WAITING_STAFF
    queryset = ChatConversation.objects.filter(status=status).select_related('customer', 'assigned_staff').annotate(last_activity=Max('messages__created_at'))
    queryset = queryset.order_by('started_at', 'id') if status == Status.WAITING_STAFF else queryset.order_by('-last_activity', '-id')
    page = Paginator(queryset, 20).get_page(request.GET.get('page'))
    counts = dict(ChatConversation.objects.values('status').annotate(total=Count('id')).values_list('status', 'total'))
    return {'queue_page': page, 'current_status': status, 'status_tabs': [{'value': value, 'label': label, 'count': counts.get(value, 0)} for value, label in Status.choices]}


def staff_page(request, conversation=None, form=None, status=200):
    context = queue_context(request)
    context.update(thread_context(conversation, True))
    context.update(form=form if form is not None else ChatContentForm(), chat_tab='inbox')
    if is_admin(request.user):
        context['knowledge_preview'] = ChatFAQ.objects.order_by('-updated_at', 'id')[:5]
    return render(request, 'chat/staff_workspace.html', context, status=status)


@never_cache
@login_required
@role_required(ROLE_SALES_STAFF)
def staff_inbox_view(request):
    return staff_page(request)


@never_cache
@login_required
@role_required(ROLE_SALES_STAFF)
def staff_conversation_view(request, conversation_id):
    conversation = get_object_or_404(ChatConversation.objects.select_related('customer', 'assigned_staff'), pk=conversation_id)
    form = ChatContentForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST':
        if form.is_valid():
            try:
                services.staff_reply(conversation.pk, request.user, form.cleaned_data['content'])
                return redirect(reverse('chat:staff_conversation', args=[conversation.pk]) + f'?status={Status.STAFF}')
            except services.ChatError as error:
                form.add_error(None, str(error))
        conversation.refresh_from_db()
        return staff_page(request, conversation, form, status=400)
    return staff_page(request, conversation, form)


@login_required
@role_required(ROLE_SALES_STAFF)
@require_POST
def staff_close_view(request, conversation_id):
    conversation = get_object_or_404(ChatConversation, pk=conversation_id)
    if conversation.assigned_staff_id and conversation.assigned_staff_id != request.user.pk and not is_admin(request.user):
        raise PermissionDenied('Chỉ nhân viên phụ trách hoặc admin được kết thúc hội thoại này.')
    try:
        services.close_conversation(conversation.pk, actor=request.user)
    except services.ChatError as error:
        raise PermissionDenied(str(error)) from error
    messages.success(request, 'Đã kết thúc hội thoại.')
    return redirect('chat:staff_inbox')


def snapshot_response(request, conversation, staff):
    context = thread_context(conversation, staff)
    context['embedded'] = request.GET.get('embed') == '1'
    body = {
        'conversation_id': conversation.pk if conversation else None,
        'status': conversation.status if conversation else Status.BOT,
        'status_label': conversation.get_status_display() if conversation else 'Trả lời tự động',
        'last_id': context['chat_messages'][-1].pk if context['chat_messages'] else 0,
        'thread': render_to_string('chat/includes/thread.html', context, request=request),
        'can_reply': bool(conversation and conversation.accepts_staff_reply and conversation.assigned_staff_id in (None, request.user.pk)) if staff else True,
    }
    if staff:
        context.update(queue_context(request))
        body['queue'] = render_to_string('chat/includes/queue.html', context, request=request)
    return JsonResponse(body)


@never_cache
@require_GET
@login_required
@role_required(ROLE_SALES_STAFF)
def staff_snapshot_view(request):
    identifier = request.GET.get('conversation')
    if identifier and not identifier.isdigit():
        raise Http404('Không tìm thấy hội thoại.')
    conversation = get_object_or_404(ChatConversation, pk=identifier) if identifier else None
    return snapshot_response(request, conversation, True)


@login_required
@role_required(ROLE_ADMIN)
def knowledge_list_view(request):
    query = request.GET.get('q', '').strip()[:100]
    state = request.GET.get('state', '')
    queryset = ChatFAQ.objects.all().order_by('-priority', '-updated_at', 'id')
    if query:
        queryset = queryset.filter(Q(question__icontains=query) | Q(keywords__icontains=query) | Q(category__icontains=query))
    if state in ('published', 'draft'):
        queryset = queryset.filter(is_active=state == 'published')
    return render(request, 'chat/knowledge_list.html', {'chat_tab': 'knowledge', 'page_obj': Paginator(queryset, 15).get_page(request.GET.get('page')), 'q': query, 'state': state, 'total': ChatFAQ.objects.count(), 'published': ChatFAQ.objects.filter(is_active=True).count()})


@login_required
@role_required(ROLE_ADMIN)
def knowledge_edit_view(request, faq_id=None):
    faq = get_object_or_404(ChatFAQ, pk=faq_id) if faq_id else None
    form = KnowledgeForm(request.POST if request.method == 'POST' else None, instance=faq)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Đã lưu nội dung tri thức.')
        return redirect('chat:knowledge_list')
    return render(request, 'chat/knowledge_form.html', {'chat_tab': 'knowledge', 'form': form, 'faq': faq}, status=400 if request.method == 'POST' else 200)


@login_required
@role_required(ROLE_ADMIN)
@require_POST
def knowledge_toggle_view(request, faq_id):
    faq = get_object_or_404(ChatFAQ, pk=faq_id)
    target = request.POST.get('state')
    if target not in ('published', 'draft'):
        return HttpResponse('Trạng thái không hợp lệ.', status=400)
    faq.is_active = target == 'published'
    faq.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, 'Đã cập nhật trạng thái công bố.')
    return redirect('chat:knowledge_list')


@never_cache
@login_required
@role_required(ROLE_ADMIN)
@require_GET
def knowledge_export_view(request):
    queryset = ChatFAQ.objects.all().order_by('knowledge_id')
    csv_format = request.GET.get('format') == 'csv'
    response = HttpResponse(export_csv(queryset) if csv_format else export_json(queryset), content_type='text/csv; charset=utf-8' if csv_format else 'application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="chat-knowledge.{"csv" if csv_format else "json"}"'
    return response


@login_required
@role_required(ROLE_ADMIN)
def knowledge_import_view(request):
    form = KnowledgeImportForm(request.POST if request.method == 'POST' else None, request.FILES if request.method == 'POST' else None)
    context = {'chat_tab': 'knowledge', 'form': form}
    if request.method == 'POST' and request.POST.get('action') == 'confirm':
        pending = request.session.get('chat_knowledge_preview')
        if not pending or request.POST.get('token') != pending['token'] or time.time() - pending['time'] > 900:
            messages.error(request, 'Bản xem trước không còn hiệu lực. Vui lòng tải tệp lên lại.')
            return redirect('chat:knowledge_import')
        try:
            counts = import_records(pending['records'], overwrite=request.POST.get('overwrite') == 'yes', expected=pending['revisions'])
        except KnowledgeError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f'Đã thêm {counts["created"]}, cập nhật {counts["updated"]}, không đổi {counts["unchanged"]}, bỏ qua {counts["skipped"]}.')
        request.session.pop('chat_knowledge_preview', None)
        return redirect('chat:knowledge_list')
    if request.method == 'POST' and form.is_valid():
        upload = form.cleaned_data['file']
        try:
            records = parse_knowledge(upload.read(), upload.name)
            rows, revisions = preview_records(records)
        except KnowledgeError as error:
            form.add_error('file', str(error))
        else:
            token = str(uuid.uuid4())
            request.session['chat_knowledge_preview'] = {'records': records, 'revisions': revisions, 'token': token, 'time': time.time()}
            context.update(preview=rows, token=token)
    return render(request, 'chat/knowledge_import.html', context, status=400 if form.errors else 200)


@login_required
@role_required(ROLE_ADMIN)
def knowledge_test_view(request):
    form = ChatContentForm(request.POST if request.method == 'POST' else None)
    result = None
    if request.method == 'POST' and form.is_valid():
        result = resolve_question(form.cleaned_data['content'])
    return render(request, 'chat/knowledge_test.html', {'chat_tab': 'test', 'form': form, 'result': result}, status=400 if form.errors else 200)
