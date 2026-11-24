from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CODMigrationTests(TransactionTestCase):
    old_target = [('payments', '0008_refund_audit_note')]
    new_target = [('payments', '0009_cod_only')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old_target)
        self.old_apps = executor.loader.project_state(self.old_target).apps
        user = self.old_apps.get_model('auth', 'User').objects.create(username='migration_customer')
        self.order = self.old_apps.get_model('orders', 'Order').objects.create(
            customer=user, order_code='MIGRATION-COD', recipient_name='Migration Customer',
            recipient_phone='0987654321', shipping_address='Migration test address',
            order_status='PENDING_CONFIRMATION', subtotal=Decimal('12000000'),
            discount_total=Decimal('0'), grand_total=Decimal('12000000'),
        )
        self.payment = self.old_apps.get_model('payments', 'Payment').objects.create(
            order=self.order, method='COD', status='UNPAID',
            expected_amount=Decimal('12000000'), notes='Preserve this payment',
        )

    def tearDown(self):
        if 'payments_paymenttransaction' in connection.introspection.table_names():
            self.old_apps.get_model('payments', 'PaymentReconciliation').objects.all().delete()
            self.old_apps.get_model('payments', 'PaymentTransaction').objects.all().delete()
            self.old_apps.get_model('payments', 'Payment').objects.all().update(method='COD')
        MigrationExecutor(connection).migrate(self.new_target)
        super().tearDown()

    def test_upgrade_preserves_cod_order_and_payment(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.new_target)
        apps = executor.loader.project_state(self.new_target).apps
        payment = apps.get_model('payments', 'Payment').objects.get(pk=self.payment.pk)
        order = apps.get_model('orders', 'Order').objects.get(pk=self.order.pk)
        self.assertEqual(payment.expected_amount, Decimal('12000000'))
        self.assertEqual(payment.notes, 'Preserve this payment')
        self.assertEqual((payment.method, payment.status), ('COD', 'UNPAID'))
        self.assertEqual(order.order_status, 'PENDING_CONFIRMATION')
        self.assertNotIn('payments_paymenttransaction', connection.introspection.table_names())
        self.assertNotIn('payments_paymentreconciliation', connection.introspection.table_names())

    def test_upgrade_stops_before_deleting_legacy_payment_data(self):
        old_payment = self.old_apps.get_model('payments', 'Payment')
        old_payment.objects.filter(pk=self.payment.pk).update(method='QR')
        with self.assertRaisesMessage(RuntimeError, 'bảo toàn dữ liệu'):
            MigrationExecutor(connection).migrate(self.new_target)
        self.assertEqual(old_payment.objects.get(pk=self.payment.pk).method, 'QR')
        self.assertIn('payments_paymenttransaction', connection.introspection.table_names())

    def test_upgrade_stops_before_deleting_existing_bank_transactions(self):
        transaction_model = self.old_apps.get_model('payments', 'PaymentTransaction')
        transaction = transaction_model.objects.create(
            payment=self.payment, bank_transaction_ref='LEGACY-RECEIPT',
            received_amount=Decimal('12000000'), verification_status='VERIFIED',
        )
        with self.assertRaisesMessage(RuntimeError, 'bảo toàn dữ liệu'):
            MigrationExecutor(connection).migrate(self.new_target)
        self.assertTrue(transaction_model.objects.filter(pk=transaction.pk).exists())
        self.assertTrue(self.old_apps.get_model('payments', 'Payment').objects.filter(pk=self.payment.pk).exists())
