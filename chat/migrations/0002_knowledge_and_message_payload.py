import uuid

from django.db import migrations, models
from django.utils import timezone


def assign_identifiers(apps, schema_editor):
    faq_model = apps.get_model('chat', 'ChatFAQ')
    for item in faq_model.objects.using(schema_editor.connection.alias).all().iterator():
        item.knowledge_id = uuid.uuid4()
        item.save(update_fields=['knowledge_id'])


class Migration(migrations.Migration):
    dependencies = [('chat', '0001_initial')]

    operations = [
        migrations.AddField('chatfaq', 'knowledge_id', models.UUIDField(null=True, editable=False)),
        migrations.RunPython(assign_identifiers, migrations.RunPython.noop),
        migrations.AlterField('chatfaq', 'knowledge_id', models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
        migrations.AddField('chatfaq', 'category', models.CharField(default='Hỗ trợ chung', max_length=80, verbose_name='Chủ đề')),
        migrations.AddField('chatfaq', 'priority', models.PositiveSmallIntegerField(default=0, verbose_name='Mức ưu tiên')),
        migrations.AddField('chatfaq', 'updated_at', models.DateTimeField(auto_now=True, default=timezone.now, verbose_name='Cập nhật lúc'), preserve_default=False),
        migrations.AlterField('chatfaq', 'keywords', models.CharField(blank=True, null=True, max_length=1000, verbose_name='Từ khóa', help_text='Các từ khóa cách nhau bằng dấu phẩy. Ví dụ: bảo hành, đổi trả')),
        migrations.AddField('chatmessage', 'payload', models.JSONField(default=dict, blank=True)),
    ]
