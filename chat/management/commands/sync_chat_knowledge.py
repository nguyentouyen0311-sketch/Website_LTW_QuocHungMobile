from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chat.knowledge import KnowledgeError, export_json, import_records, parse_knowledge, preview_records
from chat.models import ChatFAQ


class Command(BaseCommand):
    help = 'Nạp/cập nhật bộ tri thức JSON hoặc CSV; không tự ghi đè nội dung đã có.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(Path(__file__).resolve().parents[2] / 'data' / 'knowledge.json'))
        parser.add_argument('--update-existing', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--export', metavar='OUTPUT_JSON')

    def handle(self, *args, **options):
        if options['export']:
            try:
                Path(options['export']).write_text(export_json(ChatFAQ.objects.order_by('knowledge_id')), encoding='utf-8')
            except OSError as error:
                raise CommandError(str(error)) from error
            self.stdout.write(self.style.SUCCESS('Đã xuất bộ tri thức. Không bao gồm lịch sử chat hoặc tài khoản.'))
            return
        path = Path(options['path'])
        try:
            records = parse_knowledge(path.read_bytes(), path.name, max_entries=None, max_bytes=None)
            if options['dry_run']:
                rows, revisions = preview_records(records)
                for row in rows:
                    self.stdout.write(f'{row["action"]}: {row["record"]["question"]}')
                return
            counts = import_records(records, overwrite=options['update_existing'])
        except (OSError, KnowledgeError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f'Thêm {counts["created"]}, cập nhật {counts["updated"]}, không đổi {counts["unchanged"]}, bỏ qua {counts["skipped"]}.'))
