from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0004_alter_returnrequest_reason_code_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='returnrequest',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Thời điểm hoàn tất'),
        ),
        migrations.AddField(
            model_name='returnrequest',
            name='completed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_return_requests', to=settings.AUTH_USER_MODEL, verbose_name='Người xác nhận hoàn tất'),
        ),
    ]
