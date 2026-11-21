"""Kiểm thử luồng phía khách: TC26 (FAQ tự động) và TC27 (ngoài FAQ chuyển nhân viên)."""
from django.test import TestCase
from django.urls import reverse

from chat.models import ChatConversation, ChatFAQ, ChatMessage
from chat.test_support import make_admin, make_customer, make_inventory, make_sales

Status = ChatConversation.Status
Sender = ChatMessage.SenderType


class ChatPageTests(TestCase):
    def setUp(self):
        self.faq = ChatFAQ.objects.create(
            question='Chính sách bảo hành thế nào?', answer='Bảo hành 12 tháng.', keywords='bảo hành',
        )

    def test_anonymous_can_open_chat_page_and_sees_faq(self):
        response = self.client.get(reverse('chat:chat'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chính sách bảo hành thế nào?')

    def test_opening_page_does_not_create_conversation(self):
        self.client.get(reverse('chat:chat'))
        self.assertEqual(ChatConversation.objects.count(), 0)

    def test_inactive_faq_is_not_listed(self):
        ChatFAQ.objects.create(question='Câu đã tắt', answer='x', is_active=False)
        response = self.client.get(reverse('chat:chat'))
        self.assertNotContains(response, 'Câu đã tắt')

    def test_action_urls_require_post(self):
        self.assertEqual(self.client.get(reverse('chat:ask_faq', args=[self.faq.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse('chat:send')).status_code, 405)
        self.assertEqual(self.client.get(reverse('chat:close')).status_code, 405)


class FaqFlowTests(TestCase):
    """TC26: khách chọn/nhập câu FAQ thì hệ thống trả lời theo kịch bản."""

    def setUp(self):
        self.faq = ChatFAQ.objects.create(
            question='Chính sách bảo hành thế nào?', answer='Bảo hành 12 tháng.', keywords='bảo hành',
        )

    def test_guest_picks_faq_gets_bot_answer(self):
        response = self.client.post(reverse('chat:ask_faq', args=[self.faq.pk]))
        self.assertRedirects(response, reverse('chat:chat'), fetch_redirect_response=False)

        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.guest_session, self.client.session.session_key)
        self.assertIsNone(conversation.customer)
        self.assertEqual(conversation.status, Status.BOT)

        customer_msg, bot_msg = list(conversation.messages.all())
        self.assertEqual(customer_msg.sender_type, Sender.CUSTOMER)
        self.assertEqual(customer_msg.content, self.faq.question)
        self.assertEqual(bot_msg.sender_type, Sender.BOT)
        self.assertEqual(bot_msg.content, 'Bảo hành 12 tháng.')
        self.assertEqual(bot_msg.faq, self.faq)

    def test_logged_in_customer_conversation_belongs_to_customer(self):
        customer = make_customer()
        self.client.force_login(customer)
        self.client.post(reverse('chat:ask_faq', args=[self.faq.pk]))
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.customer, customer)
        self.assertIsNone(conversation.guest_session)

    def test_inactive_faq_returns_404(self):
        inactive = ChatFAQ.objects.create(question='Cũ', answer='x', is_active=False)
        response = self.client.post(reverse('chat:ask_faq', args=[inactive.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ChatConversation.objects.count(), 0)

    def test_free_text_matching_keyword_is_answered_automatically(self):
        # Gõ không dấu vẫn khớp từ khóa "bảo hành"
        self.client.post(reverse('chat:send'), {'content': 'Cho minh hoi bao hanh may bao lau?'})
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.status, Status.BOT)
        last = conversation.messages.last()
        self.assertEqual(last.sender_type, Sender.BOT)
        self.assertEqual(last.faq, self.faq)


class EscalateToStaffTests(TestCase):
    """TC27: câu hỏi ngoài FAQ được chuyển nhân viên bán hàng."""

    def setUp(self):
        ChatFAQ.objects.create(question='Bảo hành?', answer='12 tháng', keywords='bảo hành')

    def test_unmatched_text_moves_to_waiting_staff_with_notice(self):
        self.client.post(reverse('chat:send'), {'content': 'Máy này có màu hồng không?'})
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.status, Status.WAITING_STAFF)
        customer_msg, bot_msg = list(conversation.messages.all())
        self.assertEqual(customer_msg.sender_type, Sender.CUSTOMER)
        self.assertEqual(customer_msg.content, 'Máy này có màu hồng không?')
        self.assertEqual(bot_msg.sender_type, Sender.BOT)
        self.assertIn('nhân viên', bot_msg.content)

    def test_second_unmatched_message_does_not_repeat_notice(self):
        url = reverse('chat:send')
        self.client.post(url, {'content': 'Máy này có màu hồng không?'})
        self.client.post(url, {'content': 'Mình cần tư vấn thêm'})
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.messages.filter(sender_type=Sender.BOT).count(), 1)
        self.assertEqual(conversation.messages.filter(sender_type=Sender.CUSTOMER).count(), 2)
        self.assertEqual(conversation.status, Status.WAITING_STAFF)

    def test_faq_not_answered_while_waiting_for_staff(self):
        url = reverse('chat:send')
        self.client.post(url, {'content': 'Máy này có màu hồng không?'})
        self.client.post(url, {'content': 'Cho mình hỏi bảo hành'})
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.messages.filter(sender_type=Sender.BOT).count(), 1)

    def test_empty_message_is_rejected(self):
        self.client.post(reverse('chat:send'), {'content': '   '})
        self.assertEqual(ChatConversation.objects.count(), 0)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_too_long_message_is_rejected(self):
        self.client.post(reverse('chat:send'), {'content': 'a' * 1001})
        self.assertEqual(ChatConversation.objects.count(), 0)
        self.assertEqual(ChatMessage.objects.count(), 0)


class OwnershipAndCloseTests(TestCase):
    def test_customer_does_not_see_other_customers_messages(self):
        first = make_customer('khachA')
        second = make_customer('khachB')
        self.client.force_login(first)
        self.client.post(reverse('chat:send'), {'content': 'Tin nhắn riêng của khách A'})
        self.client.logout()

        self.client.force_login(second)
        response = self.client.get(reverse('chat:chat'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Tin nhắn riêng của khách A')

    def test_customer_sees_own_open_conversation(self):
        customer = make_customer()
        self.client.force_login(customer)
        self.client.post(reverse('chat:send'), {'content': 'Tin nhắn của tôi'})
        response = self.client.get(reverse('chat:chat'))
        self.assertContains(response, 'Tin nhắn của tôi')

    def test_close_then_new_message_starts_new_conversation(self):
        self.client.post(reverse('chat:send'), {'content': 'Câu hỏi đầu tiên'})
        first = ChatConversation.objects.get()

        self.client.post(reverse('chat:close'))
        first.refresh_from_db()
        self.assertEqual(first.status, Status.CLOSED)
        self.assertIsNotNone(first.closed_at)

        response = self.client.get(reverse('chat:chat'))
        self.assertNotContains(response, 'Câu hỏi đầu tiên')

        self.client.post(reverse('chat:send'), {'content': 'Câu hỏi thứ hai'})
        self.assertEqual(ChatConversation.objects.count(), 2)
        self.assertEqual(ChatConversation.objects.exclude(status=Status.CLOSED).count(), 1)

    def test_close_without_open_conversation_is_harmless(self):
        response = self.client.post(reverse('chat:close'))
        self.assertRedirects(response, reverse('chat:chat'), fetch_redirect_response=False)
        self.assertEqual(ChatConversation.objects.count(), 0)


class RoleGuardTests(TestCase):
    def test_inventory_staff_cannot_use_customer_chat(self):
        self.client.force_login(make_inventory())
        self.assertEqual(self.client.get(reverse('chat:chat')).status_code, 403)
        self.assertEqual(self.client.post(reverse('chat:send'), {'content': 'xin chào'}).status_code, 403)
        self.assertEqual(ChatConversation.objects.count(), 0)

    def test_sales_staff_is_redirected_to_inbox(self):
        self.client.force_login(make_sales())
        response = self.client.get(reverse('chat:chat'))
        self.assertRedirects(response, reverse('chat:staff_inbox'), fetch_redirect_response=False)

    def test_sales_staff_cannot_create_customer_conversation(self):
        self.client.force_login(make_sales())
        self.client.post(reverse('chat:send'), {'content': 'xin chào'})
        self.assertEqual(ChatConversation.objects.count(), 0)

    def test_admin_is_redirected_to_inbox(self):
        self.client.force_login(make_admin())
        response = self.client.get(reverse('chat:chat'))
        self.assertRedirects(response, reverse('chat:staff_inbox'), fetch_redirect_response=False)
