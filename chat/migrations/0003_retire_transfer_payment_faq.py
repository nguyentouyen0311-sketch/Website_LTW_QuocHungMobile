import re
import unicodedata

from django.db import migrations


def retire_transfer_payment_faq(apps, schema_editor):
    faq_model = apps.get_model('chat', 'ChatFAQ')
    database = schema_editor.connection.alias
    retired_ids = []
    for faq in faq_model.objects.using(database).filter(is_active=True).iterator():
        content = ' '.join((faq.question or '', faq.answer or '', faq.keywords or ''))
        content = ''.join(
            character for character in unicodedata.normalize('NFKD', content.casefold())
            if not unicodedata.combining(character)
        ).replace('đ', 'd')
        if re.search(r'\b(?:qr|vietqr)\b', content) or any(
            phrase in content for phrase in ('thanh toan chuyen khoan', 'chuyen khoan thanh toan')
        ):
            retired_ids.append(faq.pk)
    faq_model.objects.using(database).filter(pk__in=retired_ids).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [('chat', '0002_knowledge_and_message_payload')]

    operations = [
        migrations.RunPython(retire_transfer_payment_faq, migrations.RunPython.noop),
    ]
