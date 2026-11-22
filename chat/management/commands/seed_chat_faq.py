from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Nạp bộ tri thức chuẩn; tương thích lệnh seed_chat_faq cũ, không ghi đè nội dung hiện có.'

    def handle(self, *args, **options):
        call_command('sync_chat_knowledge', stdout=self.stdout)
