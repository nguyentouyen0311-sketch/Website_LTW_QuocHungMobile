from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('payments', '0007_refund_return_request')]

    operations = [
        migrations.AddField(
            model_name='refund',
            name='audit_note',
            field=models.TextField(blank=True, help_text='Ghi chú xác nhận nội bộ cho giao dịch hoàn tiền.', max_length=1000, verbose_name='Ghi chú kiểm toán'),
        ),
    ]
