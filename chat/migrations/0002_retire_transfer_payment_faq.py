from django.db import migrations
from django.db.models import Q


def retire_transfer_faq(apps, schema_editor):
    faq = apps.get_model('chat', 'ChatFAQ')
    manager = faq.objects.using(schema_editor.connection.alias)
    manager.filter(Q(question__icontains='vietqr') | Q(answer__icontains='vietqr') | Q(keywords__icontains='xác nhận qr')).update(is_active=False)
    manager.filter(question='Có những hình thức thanh toán nào?').update(
        answer='Cửa hàng hỗ trợ thanh toán khi nhận hàng (COD). Bạn kiểm tra sản phẩm và thanh toán tiền mặt khi nhận hàng.',
        keywords='hình thức thanh toán, phương thức thanh toán, thanh toán cod',
        is_active=True,
    )


class Migration(migrations.Migration):
    dependencies = [('chat', '0001_initial')]
    operations = [migrations.RunPython(retire_transfer_faq, migrations.RunPython.noop)]
