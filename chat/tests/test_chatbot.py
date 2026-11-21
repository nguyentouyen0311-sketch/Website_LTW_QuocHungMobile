import io
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from catalog.models import Category, Product, ProductVariant
from chat import services
from chat.engine import resolve_question
from chat.knowledge import KnowledgeError, export_csv, export_json, import_records, parse_knowledge, preview_records, record_for
from chat.models import ChatConversation, ChatFAQ, ChatMessage


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ChatbotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('chat_admin', password='test-pass')
        cls.sales = get_user_model().objects.create_user('chat_sales', password='test-pass')
        cls.sales2 = get_user_model().objects.create_user('chat_sales2', password='test-pass')
        group = Group.objects.create(name='Sales Staff')
        cls.sales.groups.add(group)
        cls.sales2.groups.add(group)
        cls.customer = get_user_model().objects.create_user('chat_customer', password='test-pass')
        cls.other = get_user_model().objects.create_user('chat_other', password='test-pass')
        group = Group.objects.create(name='Customer')
        cls.customer.groups.add(group)
        cls.other.groups.add(group)
        cls.faq = ChatFAQ.objects.create(question='Thanh toán COD thế nào?', answer='Thanh toán khi nhận hàng.', keywords='cod, trả tiền khi nhận hàng', category='Thanh toán')
        cls.warranty = ChatFAQ.objects.create(question='Hỗ trợ bảo hành', answer='Nhân viên kiểm tra điều kiện bảo hành.', keywords='bảo hành', category='Bảo hành')
        cls.draft = ChatFAQ.objects.create(question='Nội dung bí mật', answer='Không được công bố', keywords='nội dung bí mật', is_active=False)
        cls.category = Category.objects.create(name='Điện thoại')
        cls.product = Product.objects.create(name='iPhone 16', category=cls.category, brand='Apple', description='Màn hình OLED')
        cls.variant = ProductVariant.objects.create(product=cls.product, sku='IP16-256', price=20000000, combination_key='256')

    def test_keyword_normalization_and_boundaries(self):
        for question in ('Có COD không?', 'TRẢ TIỀN KHI NHẬN HÀNG?', 'tra tien khi nhan hang'):
            with self.subTest(question=question):
                result = resolve_question(question)
                self.assertEqual(result['faq'], self.faq)
        self.assertEqual(resolve_question('codec')['kind'], 'handoff')

    def test_unpublished_content_is_not_used(self):
        self.assertEqual(resolve_question('nội dung bí mật')['kind'], 'handoff')
        response = self.client.get(reverse('chat:chat'))
        self.assertNotContains(response, self.draft.question)

    def test_ambiguous_topics_prompt_choice(self):
        result = resolve_question('COD và bảo hành')
        self.assertEqual(result['kind'], 'choices')
        self.assertEqual({row['id'] for row in result['payload']['choices']}, {self.faq.pk, self.warranty.pk})

    def test_priority_is_explicit_and_deterministic(self):
        self.warranty.priority = 10
        self.warranty.save()
        self.assertEqual(resolve_question('cod và bảo hành')['faq'], self.warranty)

    def test_case_specific_and_human_request_escalate(self):
        for question in ('Đổi địa chỉ nhận hàng giúp tôi', 'Gặp nhân viên', 'Tôi đã chuyển khoản nhưng chưa thấy xác nhận'):
            self.assertEqual(resolve_question(question)['kind'], 'handoff')

    def test_product_price_is_current_and_only_public(self):
        result = resolve_question('Giá iPhone 16 bao nhiêu?')
        self.assertEqual(result['kind'], 'products')
        self.assertEqual(result['payload']['products'][0]['variants'][0]['price'], '20.000.000đ')
        self.variant.price = 19000000
        self.variant.save()
        self.assertEqual(resolve_question('Giá iPhone 16')['payload']['products'][0]['variants'][0]['price'], '19.000.000đ')
        self.product.is_deleted = True
        self.product.save()
        self.assertNotEqual(resolve_question('iPhone 16')['kind'], 'products')

    def test_hidden_categories_and_variants_not_disclosed(self):
        self.category.is_visible = False
        self.category.save()
        self.assertNotEqual(resolve_question('Giá iPhone 16')['kind'], 'products')
        self.category.is_visible = True
        self.category.save()
        self.variant.is_active = False
        self.variant.save()
        self.assertNotEqual(resolve_question('Giá iPhone 16')['kind'], 'products')

    def test_stock_questions_never_return_inventory(self):
        for question in ('iPhone 16 còn hàng không?', 'Tồn kho iPhone 16', 'iPhone 16 còn bao nhiêu?'):
            result = resolve_question(question)
            self.assertEqual(result['kind'], 'stock')
            self.assertNotIn('products', result.get('payload', {}))

    def test_guest_questions_start_once_and_poll_without_writes(self):
        self.assertEqual(self.client.get(reverse('chat:chat')).status_code, 200)
        self.assertEqual(ChatConversation.objects.count(), 0)
        self.client.post(reverse('chat:send'), {'content': 'Có COD không?'})
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(conversation.status, 'BOT')
        for unused in range(2):
            response = self.client.get(reverse('chat:updates'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['last_id'], conversation.messages.last().pk)
            self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(conversation.messages.count(), 2)

    def test_embedded_chat_preserves_mode_and_allows_only_same_origin_frames(self):
        response = self.client.get(reverse('chat:chat') + '?embed=1')
        self.assertContains(response, 'chat-embed')
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        self.assertNotContains(response, 'class="cart-header"')
        response = self.client.post(reverse('chat:send') + '?embed=1', {'content': 'cod'})
        self.assertEqual(response.url, reverse('chat:chat') + '?embed=1')
        response = self.client.post(reverse('chat:send') + '?embed=1', {'content': 'x' * 1001})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_ambiguous_choices_preserve_embedded_mode(self):
        self.client.post(reverse('chat:send') + '?embed=1', {'content': 'cod và bảo hành'})
        response = self.client.get(reverse('chat:updates') + '?embed=1')
        self.assertIn(f'/chat/faq/{self.faq.pk}/?embed=1', response.json()['thread'])

    def test_cart_integrates_shared_launcher_without_changing_cart(self):
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'qhm-chat-launcher')
        self.assertContains(response, 'chat/js/launcher.js')

    def test_guest_and_authenticated_conversation_isolation(self):
        self.client.post(reverse('chat:send'), {'content': 'cod'})
        guest_conversation = ChatConversation.objects.get()
        stranger = Client()
        self.assertEqual(stranger.get(reverse('chat:updates'), {'conversation': guest_conversation.pk}).status_code, 404)
        conversation = ChatConversation.objects.create(customer=self.customer)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('chat:updates'), {'conversation': conversation.pk}).status_code, 404)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('chat:updates'), {'conversation': conversation.pk}).status_code, 200)

    def test_invalid_identifiers_are_404(self):
        self.assertEqual(self.client.get(reverse('chat:updates'), {'conversation': 'bad'}).status_code, 404)
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse('chat:staff_updates'), {'conversation': 'bad'}).status_code, 404)

    def test_handoff_is_idempotent_and_staff_reply_is_live(self):
        self.client.post(reverse('chat:handoff'))
        self.client.post(reverse('chat:handoff'))
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.messages.count(), 1)
        self.client.post(reverse('chat:send'), {'content': 'cod'})
        self.assertEqual(conversation.messages.count(), 2)
        services.staff_reply(conversation.pk, self.sales, 'Xin chào, tôi có thể giúp gì?')
        response = self.client.get(reverse('chat:updates'))
        self.assertEqual(response.json()['status'], 'STAFF')
        self.assertIn('Xin chào', response.json()['thread'])
        with self.assertRaises(services.ChatError):
            services.staff_reply(conversation.pk, self.sales2, 'Nhân viên khác')
        self.assertEqual(conversation.messages.count(), 3)

    def test_closed_conversation_preserves_history_and_new_chat_starts(self):
        self.client.post(reverse('chat:send'), {'content': 'cod'})
        original = ChatConversation.objects.get()
        response = self.client.post(reverse('chat:close'), follow=True)
        self.assertContains(response, self.faq.question)
        self.client.post(reverse('chat:send'), {'content': 'cod'})
        original.refresh_from_db()
        self.assertEqual(original.status, 'CLOSED')
        self.assertEqual(original.messages.count(), 2)
        self.assertEqual(ChatConversation.objects.count(), 2)

    def test_invalid_message_keeps_input_and_does_not_write(self):
        content = 'Nội dung cần giữ ' * 100
        response = self.client.post(reverse('chat:send'), {'content': content})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['form']['content'].value(), content)
        self.assertEqual(ChatConversation.objects.count(), 0)

    def test_messages_escape_html(self):
        self.client.post(reverse('chat:send'), {'content': '<script>alert(1)</script>'})
        response = self.client.get(reverse('chat:updates'))
        self.assertNotIn('<script>alert(1)</script>', response.json()['thread'])
        self.assertIn('&lt;script&gt;', response.json()['thread'])

    def test_csrf_and_post_required(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('chat:send'), {'content': 'cod'}).status_code, 403)
        for route in ('send', 'close', 'handoff'):
            self.assertEqual(self.client.get(reverse('chat:' + route)).status_code, 405)

    def test_admin_only_knowledge_staff_only_inbox(self):
        routes = ('knowledge_list', 'knowledge_create', 'knowledge_import', 'knowledge_export', 'knowledge_test')
        for user in (self.customer, self.sales):
            self.client.force_login(user)
            for route in routes:
                self.assertEqual(self.client.get(reverse('chat:' + route)).status_code, 403)
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse('chat:staff_inbox')).status_code, 200)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse('chat:staff_inbox')).status_code, 403)

    def test_all_admin_pages_render_and_preview_has_no_chat_side_effects(self):
        self.client.force_login(self.admin)
        for route in ('staff_inbox', 'knowledge_list', 'knowledge_create', 'knowledge_import', 'knowledge_test'):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse('chat:' + route)).status_code, 200)
        response = self.client.post(reverse('chat:knowledge_test'), {'content': 'cod'})
        self.assertContains(response, self.faq.answer)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_knowledge_validation_keeps_input_and_state_is_explicit(self):
        self.client.force_login(self.admin)
        data = {'question': 'Câu cần giữ', 'category': 'Chủ đề', 'answer': 'Nội dung', 'keywords': 'x', 'priority': 0}
        response = self.client.post(reverse('chat:knowledge_create'), data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Câu cần giữ', status_code=400)
        data['keywords'] = 'mở cửa, mo cua'
        self.client.post(reverse('chat:knowledge_create'), data)
        faq = ChatFAQ.objects.get(question='Câu cần giữ')
        self.assertFalse(faq.is_active)
        for unused in range(2):
            self.client.post(reverse('chat:knowledge_toggle', args=[faq.pk]), {'state': 'published'})
        faq.refresh_from_db()
        self.assertTrue(faq.is_active)
        self.assertEqual(faq.keywords, 'mở cửa')

    def test_json_roundtrip_idempotence_and_explicit_updates(self):
        records = parse_knowledge(export_json(ChatFAQ.objects.all()).encode(), 'data.json')
        self.assertEqual(import_records(records)['unchanged'], 3)
        records[0]['answer'] = 'Nội dung mới'
        self.assertEqual(import_records(records)['skipped'], 1)
        self.faq.refresh_from_db()
        self.assertNotEqual(self.faq.answer, 'Nội dung mới')
        self.assertEqual(import_records(records, overwrite=True)['updated'], 1)
        self.faq.refresh_from_db()
        self.assertEqual(self.faq.answer, 'Nội dung mới')

    def test_invalid_import_is_rejected_before_database_changes(self):
        entries = [record_for(self.faq), {**record_for(self.warranty), 'priority': 999}]
        with self.assertRaises(KnowledgeError):
            parse_knowledge(json.dumps({'version': 1, 'entries': entries}).encode(), 'data.json')
        for raw in (b'not json', b'{"version":1,"entries":[]}', b'{"version":1,"entries":[null]}'):
            with self.assertRaises(KnowledgeError):
                parse_knowledge(raw, 'data.json')
        self.assertEqual(ChatFAQ.objects.count(), 3)

    def test_stale_preview_rejects_whole_batch(self):
        records = [record_for(self.faq), record_for(self.warranty)]
        rows, revisions = preview_records(records)
        records[0]['answer'] = 'Bản muốn nhập'
        self.warranty.answer = 'Admin khác đã sửa'
        self.warranty.save()
        with self.assertRaises(KnowledgeError):
            import_records(records, overwrite=True, expected=revisions)
        self.faq.refresh_from_db()
        self.assertNotEqual(self.faq.answer, 'Bản muốn nhập')

    def test_csv_roundtrip_and_formula_escape(self):
        records = parse_knowledge(export_csv(ChatFAQ.objects.all()).encode('utf-8'), 'data.csv')
        self.assertEqual(len(records), 3)
        self.faq.answer = '=HYPERLINK("https://example.com")'
        self.faq.save()
        self.assertIn("'=HYPERLINK", export_csv([self.faq]))

    def test_import_requires_preview_and_preserves_knowledge_ids(self):
        self.client.force_login(self.admin)
        record = record_for(self.faq)
        record['answer'] = 'Đã cập nhật qua tệp'
        upload = SimpleUploadedFile('knowledge.json', json.dumps({'version': 1, 'entries': [record]}).encode())
        response = self.client.post(reverse('chat:knowledge_import'), {'file': upload})
        self.assertContains(response, 'Khác dữ liệu hiện tại')
        token = self.client.session['chat_knowledge_preview']['token']
        self.client.post(reverse('chat:knowledge_import'), {'action': 'confirm', 'token': token, 'overwrite': 'yes'})
        self.faq.refresh_from_db()
        self.assertEqual(self.faq.answer, record['answer'])
        self.assertEqual(str(self.faq.knowledge_id), record['knowledge_id'])
        self.assertIsNone(self.client.session.get('chat_knowledge_preview'))

    def test_export_contains_no_customer_data(self):
        ChatConversation.objects.create(customer=self.customer)
        self.client.force_login(self.admin)
        response = self.client.get(reverse('chat:knowledge_export'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.customer.username, response.content.decode())
        self.assertEqual(set(response.json()), {'version', 'entries'})

    def test_command_repeated_seed_does_not_duplicate_or_overwrite(self):
        output = io.StringIO()
        call_command('seed_chat_faq', stdout=output)
        count = ChatFAQ.objects.count()
        call_command('sync_chat_knowledge', stdout=output)
        self.assertEqual(ChatFAQ.objects.count(), count)
        faq = ChatFAQ.objects.get(knowledge_id='70000000-0000-4000-8000-000000000001')
        faq.answer = 'Nội dung riêng'
        faq.save()
        call_command('sync_chat_knowledge', stdout=output)
        faq.refresh_from_db()
        self.assertEqual(faq.answer, 'Nội dung riêng')
        call_command('sync_chat_knowledge', update_existing=True, stdout=output)
        faq.refresh_from_db()
        self.assertNotEqual(faq.answer, 'Nội dung riêng')

    def test_cli_can_read_full_exports_above_web_limit(self):
        import uuid
        records = [{**record_for(self.faq), 'knowledge_id': str(uuid.uuid4()), 'question': f'Nội dung {index}'} for index in range(201)]
        raw = json.dumps({'version': 1, 'entries': records}, ensure_ascii=False).encode('utf-8')
        with self.assertRaises(KnowledgeError):
            parse_knowledge(raw, 'full.json')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'full.json'
            source.write_bytes(raw)
            call_command('sync_chat_knowledge', str(source), dry_run=True, stdout=io.StringIO())
        self.assertEqual(ChatFAQ.objects.count(), 3)

    def test_staff_cannot_close_another_staff_conversation(self):
        conversation = ChatConversation.objects.create(customer=self.customer, status='STAFF', assigned_staff=self.sales)
        self.client.force_login(self.sales2)
        self.assertEqual(self.client.post(reverse('chat:staff_close', args=[conversation.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('chat:staff_close', args=[conversation.pk])).status_code, 302)
