"""Kiểm thử model, ràng buộc CSDL và hàm nghiệp vụ của chatbox."""
from django.db import IntegrityError, transaction
from django.test import TestCase

from chat import services
from chat.models import ChatConversation, ChatFAQ, ChatMessage
from chat.test_support import make_customer, make_sales

Status = ChatConversation.Status
Sender = ChatMessage.SenderType


class NormalizeTextTests(TestCase):
    def test_strips_vietnamese_accents_and_case(self):
        self.assertEqual(services.normalize_text('  Bảo   HÀNH  Đổi '), 'bao hanh doi')

    def test_hoi_va_hoi_dau_cung_chuan_hoa_nhu_nhau(self):
        self.assertEqual(services.normalize_text('hủy'), services.normalize_text('huỷ'))

    def test_none_and_empty(self):
        self.assertEqual(services.normalize_text(None), '')
        self.assertEqual(services.normalize_text('   '), '')

    def test_split_keywords_ignores_blank_and_too_short(self):
        self.assertEqual(
            services.split_keywords('Bảo hành, , a,\nĐổi trả'),
            ['bao hanh', 'doi tra'],
        )


class FindMatchingFaqTests(TestCase):
    def setUp(self):
        self.faq = ChatFAQ.objects.create(question='Bảo hành?', answer='12 tháng', keywords='bảo hành')
        self.inactive = ChatFAQ.objects.create(
            question='Cũ', answer='x', keywords='giao hàng', is_active=False,
        )
        self.no_keyword = ChatFAQ.objects.create(question='Không từ khóa', answer='y', keywords=None)

    def test_matches_without_accents(self):
        self.assertEqual(services.find_matching_faq('cho minh hoi bao hanh may'), self.faq)

    def test_inactive_faq_is_ignored(self):
        self.assertIsNone(services.find_matching_faq('giao hàng bao lâu'))

    def test_no_match_returns_none(self):
        self.assertIsNone(services.find_matching_faq('xin chào'))

    def test_empty_text_returns_none(self):
        self.assertIsNone(services.find_matching_faq('   '))


class ModelConstraintTests(TestCase):
    def test_conversation_requires_customer_or_guest(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatConversation.objects.create()

    def test_conversation_rejects_empty_guest_session(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatConversation.objects.create(guest_session='')

    def test_conversation_with_guest_session_is_valid(self):
        conversation = ChatConversation.objects.create(guest_session='abc123')
        self.assertEqual(conversation.status, Status.BOT)
        self.assertEqual(conversation.customer_label, 'Khách vãng lai')

    def test_conversation_with_customer_is_valid(self):
        customer = make_customer()
        conversation = ChatConversation.objects.create(customer=customer)
        self.assertEqual(conversation.customer_label, 'khach1')

    def test_staff_message_requires_sender_user(self):
        conversation = ChatConversation.objects.create(guest_session='abc123')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ChatMessage.objects.create(
                    conversation=conversation, sender_type=Sender.STAFF, content='Xin chào',
                )

    def test_staff_message_with_sender_user_is_valid(self):
        conversation = ChatConversation.objects.create(guest_session='abc123')
        staff = make_sales()
        message = ChatMessage.objects.create(
            conversation=conversation, sender_type=Sender.STAFF,
            sender_user=staff, content='Xin chào',
        )
        self.assertEqual(message.sender_user, staff)

    def test_deleting_faq_keeps_message_history(self):
        conversation = ChatConversation.objects.create(guest_session='abc123')
        faq = ChatFAQ.objects.create(question='Q', answer='A')
        message = ChatMessage.objects.create(
            conversation=conversation, sender_type=Sender.BOT, faq=faq, content='A',
        )
        faq.delete()
        message.refresh_from_db()
        self.assertIsNone(message.faq)


class ServiceRulesTests(TestCase):
    def setUp(self):
        self.conversation = ChatConversation.objects.create(guest_session='abc123')
        self.staff = make_sales()

    def test_clean_content_rules(self):
        with self.assertRaises(services.ChatError):
            services.clean_content('   ')
        with self.assertRaises(services.ChatError):
            services.clean_content('a' * (services.MAX_MESSAGE_LENGTH + 1))
        self.assertEqual(services.clean_content('  xin chào  '), 'xin chào')

    def test_close_is_idempotent(self):
        self.assertEqual(services.close_conversation(self.conversation.pk), 1)
        self.conversation.refresh_from_db()
        first_closed_at = self.conversation.closed_at
        self.assertEqual(self.conversation.status, Status.CLOSED)
        self.assertIsNotNone(first_closed_at)

        self.assertEqual(services.close_conversation(self.conversation.pk), 0)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.closed_at, first_closed_at)

    def test_customer_cannot_send_after_closed(self):
        services.close_conversation(self.conversation.pk)
        with self.assertRaises(services.ChatError):
            services.customer_send_text(self.conversation, 'Xin chào')
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_faq_only_allowed_while_bot_status(self):
        faq = ChatFAQ.objects.create(question='Q', answer='A')
        ChatConversation.objects.filter(pk=self.conversation.pk).update(status=Status.WAITING_STAFF)
        with self.assertRaises(services.ChatError):
            services.customer_ask_faq(self.conversation, faq)
        self.assertEqual(ChatMessage.objects.count(), 0)

    def test_inactive_faq_cannot_be_asked(self):
        faq = ChatFAQ.objects.create(question='Q', answer='A', is_active=False)
        with self.assertRaises(services.ChatError):
            services.customer_ask_faq(self.conversation, faq)

    def test_staff_reply_requires_waiting_or_staff_status(self):
        with self.assertRaises(services.ChatError):
            services.staff_reply(self.conversation.pk, self.staff, 'Xin chào')
        self.assertEqual(ChatMessage.objects.count(), 0)
