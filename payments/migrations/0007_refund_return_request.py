import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0004_alter_returnrequest_reason_code_and_more'),
        ('payments', '0006_codcollection'),
    ]

    operations = [
        migrations.AddField(
            model_name='refund',
            name='return_request',
            field=models.OneToOneField(blank=True, help_text='Yêu cầu đổi/trả tạo ra hồ sơ hoàn tiền này (nếu có).', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='refund_record', to='orders.returnrequest', verbose_name='Yêu cầu trả hàng'),
        ),
    ]
