"""Kiểm thử phía nhân viên: hộp thư, trả lời, đóng hội thoại và phân quyền."""
from django.test import TestCase
from django.urls import reverse

from chat.models import ChatConversation, ChatMessage
from chat.test_support import make_admin, make_customer, make_inventory, make_sales

Status = ChatConversation.Status
Sender = ChatMessage.SenderType


def make_waiting_conversation(customer=None, text='Máy này có màu hồng không?'):
    """Tạo hội thoại đã chuyển nhân viên, giống kết quả của luồng khách gửi câu ngoài FAQ."""
    if customer is None:
        conversation = ChatConversation.objects.create(guest_session='guest-key-1')
    else:
        conversation = ChatConversation.objects.create(customer=customer)
    ChatMessage.objects.create(
        conversation=conversation, sender_type=Sender.CUSTOMER,
        sender_user=customer, content=text,
    )
    conversation.status = Status.WAITING_STAFF
    conversation.save(update_fields=['status'])
    return conversation


class StaffAccessTests(TestCase):
    def setUp(self):
        self.conversation = make_waiting_conversation()
        self.inbox = reverse('chat:staff_inbox')
        self.detail = reverse('chat:staff_conversation', args=[self.conversation.pk])
        self.close = reverse('chat:staff_close', args=[self.conversation.pk])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.inbox)
        self.assertRedirects(
            response, f"{reverse('accounts:login')}?next={self.inbox}", fetch_redirect_response=False,
        )

    def test_customer_gets_403(self):
        self.client.force_login(make_customer())
        self.assertEqual(self.client.get(self.inbox).status_code, 403)
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.client.post(self.detail, {'content': 'Xin chào'}).status_code, 403)
        self.assertEqual(self.client.post(self.close).status_code, 403)
        self.assertEqual(ChatMessage.objects.filter(sender_type=Sender.STAFF).count(), 0)

    def test_inventory_staff_gets_403(self):
        self.client.force_login(make_inventory())
        self.assertEqual(self.client.get(self.inbox).status_code, 403)

    def test_sales_staff_and_admin_can_open_inbox(self):
        self.client.force_login(make_sales())
        self.assertEqual(self.client.get(self.inbox).status_code, 200)
        self.client.logout()
        self.client.force_login(make_admin())
        self.assertEqual(self.client.get(self.inbox).status_code, 200)

    def test_close_requires_post(self):
        self.client.force_login(make_sales())
        self.assertEqual(self.client.get(self.close).status_code, 405)


class StaffInboxTests(TestCase):
    def setUp(self):
        self.client.force_login(make_sales())

    def test_default_tab_lists_waiting_conversations_only(self):
        waiting = make_waiting_conversation(make_customer('khach_cho'))
        bot_only = ChatConversation.objects.create(customer=make_customer('khach_bot'))
        response = self.client.get(reverse('chat:staff_inbox'))
        self.assertContains(response, 'khach_cho')
        self.assertNotContains(response, 'khach_bot')
        self.assertContains(response, reverse('chat:staff_conversation', args=[waiting.pk]))
        self.assertEqual(bot_only.status, Status.BOT)

    def test_status_tab_filter(self):
        make_waiting_conversation(make_customer('khach_cho'))
        ChatConversation.objects.create(customer=make_customer('khach_bot'))
        response = self.client.get(reverse('chat:staff_inbox'), {'status': Status.BOT})
        self.assertContains(response, 'khach_bot')
        self.assertNotContains(response, 'khach_cho')

    def test_invalid_status_falls_back_to_waiting(self):
        make_waiting_conversation(make_customer('khach_cho'))
        response = self.client.get(reverse('chat:staff_inbox'), {'status': 'KHONG_HOP_LE'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'khach_cho')

    def test_guest_conversation_is_labeled(self):
        make_waiting_conversation()
        response = self.client.get(reverse('chat:staff_inbox'))
        self.assertContains(response, 'Khách vãng lai')


class StaffReplyTests(TestCase):
    def setUp(self):
        self.staff = make_sales('nvban_a')
        self.conversation = make_waiting_conversation(make_customer('khach1'))
        self.url = reverse('chat:staff_conversation', args=[self.conversation.pk])
        self.client.force_login(self.staff)

    def test_detail_shows_customer_message(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Máy này có màu hồng không?')

    def test_first_reply_assigns_staff_and_changes_status(self):
        response = self.client.post(self.url, {'content': 'Dạ máy có màu hồng ạ'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Status.STAFF)
        self.assertEqual(self.conversation.assigned_staff, self.staff)
        reply = self.conversation.messages.last()
        self.assertEqual(reply.sender_type, Sender.STAFF)
        self.assertEqual(reply.sender_user, self.staff)
        self.assertEqual(reply.content, 'Dạ máy có màu hồng ạ')

    def test_second_staff_reply_is_rejected_when_assigned(self):
        self.client.post(self.url, {'content': 'Dạ máy có màu hồng ạ'})
        other = make_sales('nvban_b')
        self.client.force_login(other)
        response = self.client.post(self.url, {'content': 'Em bổ sung thêm thông tin'})
        self.conversation.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.conversation.assigned_staff, self.staff)
        self.assertEqual(self.conversation.messages.filter(sender_type=Sender.STAFF).count(), 1)

    def test_admin_can_reply(self):
        admin = make_admin()
        self.client.force_login(admin)
        self.client.post(self.url, {'content': 'Admin hỗ trợ ạ'})
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_staff, admin)

    def test_customer_sees_staff_reply(self):
        self.client.post(self.url, {'content': 'Dạ máy có màu hồng ạ'})
        self.client.logout()
        self.client.force_login(self.conversation.customer)
        response = self.client.get(reverse('chat:chat'))
        self.assertContains(response, 'Dạ máy có màu hồng ạ')

    def test_empty_reply_is_rejected(self):
        response = self.client.post(self.url, {'content': '   '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.conversation.messages.filter(sender_type=Sender.STAFF).count(), 0)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Status.WAITING_STAFF)

    def test_cannot_reply_to_bot_conversation(self):
        bot_conversation = ChatConversation.objects.create(customer=make_customer('khach_bot'))
        url = reverse('chat:staff_conversation', args=[bot_conversation.pk])
        self.client.post(url, {'content': 'Xin chào'})
        self.assertEqual(bot_conversation.messages.count(), 0)
        bot_conversation.refresh_from_db()
        self.assertEqual(bot_conversation.status, Status.BOT)

    def test_cannot_reply_to_closed_conversation(self):
        ChatConversation.objects.filter(pk=self.conversation.pk).update(status=Status.CLOSED)
        self.client.post(self.url, {'content': 'Xin chào'})
        self.assertEqual(self.conversation.messages.filter(sender_type=Sender.STAFF).count(), 0)

    def test_unknown_conversation_returns_404(self):
        response = self.client.get(reverse('chat:staff_conversation', args=[99999]))
        self.assertEqual(response.status_code, 404)


class StaffCloseTests(TestCase):
    def setUp(self):
        self.conversation = make_waiting_conversation()
        self.client.force_login(make_sales())
        self.url = reverse('chat:staff_close', args=[self.conversation.pk])

    def test_staff_can_close_and_repeat_is_harmless(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('chat:staff_inbox'), fetch_redirect_response=False)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.status, Status.CLOSED)
        closed_at = self.conversation.closed_at
        self.assertIsNotNone(closed_at)

        self.client.post(self.url)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.closed_at, closed_at)

    def test_closed_conversation_history_is_kept(self):
        self.client.post(self.url)
        self.assertEqual(self.conversation.messages.count(), 1)
