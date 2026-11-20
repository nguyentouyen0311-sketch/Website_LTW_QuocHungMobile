from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from orders.tests import WorkspaceFixture
from payments.models import CODCollection, PaymentMethod, PaymentStatus
from payments.services import record_cod_collection


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CODPaymentTests(WorkspaceFixture, TestCase):
    def test_collection_is_exact_and_idempotent(self):
        order = self.make_order(stage='shipping')
        receipt = record_cod_collection(order.payment.pk, self.staff, order.grand_total, 'cod-001')
        replay = record_cod_collection(order.payment.pk, self.staff, order.grand_total, 'COD-001')
        self.assertEqual(receipt.pk, replay.pk)
        self.assertEqual(CODCollection.objects.count(), 1)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.PAID)
        self.assertEqual(receipt.reference, 'COD-001')
        with self.assertRaises(ValidationError):
            record_cod_collection(order.payment.pk, self.staff, order.grand_total, 'OTHER')

    def test_collection_rejects_underpayment_invalid_money_and_customer(self):
        order = self.make_order(stage='shipping')
        for amount in (Decimal('1'), Decimal('NaN'), True, 1.2):
            with self.assertRaises(ValidationError):
                record_cod_collection(order.payment.pk, self.staff, amount, 'COD-FAIL')
        with self.assertRaises(PermissionDenied):
            record_cod_collection(order.payment.pk, self.customer, order.grand_total, 'COD-FAIL')
        self.assertEqual(CODCollection.objects.count(), 0)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.UNPAID)

    def test_pending_cannot_collect_and_reference_not_reused(self):
        first = self.make_order()
        with self.assertRaises(ValidationError):
            record_cod_collection(first.payment.pk, self.staff, first.grand_total, 'COD-X')
        first = self.make_order(stage='shipping')
        second = self.make_order(stage='shipping')
        record_cod_collection(first.payment.pk, self.staff, first.grand_total, 'COD-X')
        with self.assertRaises(ValidationError):
            record_cod_collection(second.payment.pk, self.staff, second.grand_total, 'cod-x')

    def test_new_payment_method_only_cod(self):
        self.assertEqual(PaymentMethod.values, ['COD'])
        order = self.make_order()
        order.payment.method = 'QR'
        with self.assertRaises(ValidationError):
            order.payment.full_clean()
