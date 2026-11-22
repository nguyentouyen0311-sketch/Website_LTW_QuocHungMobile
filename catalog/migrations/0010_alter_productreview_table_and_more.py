from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0009_productreview_productreviewmedia'),
    ]

    operations = [
        migrations.AlterModelTable(
            name='productreview',
            table='catalog_productreview',
        ),
        migrations.AlterModelTable(
            name='productreviewmedia',
            table='catalog_productreviewmedia',
        ),
    ]
