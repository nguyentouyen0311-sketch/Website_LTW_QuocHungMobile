from django.db import migrations


def backfill_customer_codes(apps, schema_editor):
    CustomerProfile = apps.get_model('accounts', 'CustomerProfile')
    for profile in CustomerProfile.objects.filter(customer_code__isnull=True):
        profile.customer_code = f'KH{profile.user_id:08d}'
        profile.save(update_fields=['customer_code'])


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_customer_profile_identity')]
    operations = [migrations.RunPython(backfill_customer_codes, migrations.RunPython.noop)]
