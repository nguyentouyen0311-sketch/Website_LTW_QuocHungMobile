import csv
import hashlib
import io
import json
import uuid

from django.db import transaction

from .forms import KnowledgeForm
from .models import ChatFAQ
from .services import split_keywords


FIELDS = ('knowledge_id', 'question', 'category', 'answer', 'keywords', 'priority', 'is_active')
EDIT_FIELDS = FIELDS[1:]
MAX_ENTRIES = 200


class KnowledgeError(ValueError):
    pass


def record_for(faq):
    return {field: str(faq.knowledge_id) if field == 'knowledge_id' else getattr(faq, field) for field in FIELDS}


def fingerprint(faq):
    return hashlib.sha256(json.dumps(record_for(faq), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def equivalent(existing, incoming):
    left, right = dict(existing), dict(incoming)
    left['keywords'] = sorted(set(split_keywords(left['keywords'])))
    right['keywords'] = sorted(set(split_keywords(right['keywords'])))
    return left == right


def parse_knowledge(raw, filename, max_entries=MAX_ENTRIES, max_bytes=2 * 1024 * 1024):
    if max_bytes is not None and len(raw) > max_bytes:
        raise KnowledgeError('Tệp tối đa 2 MB.')
    try:
        text = raw.decode('utf-8-sig')
        if filename.lower().endswith('.csv'):
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or not {'question', 'answer', 'keywords'}.issubset(reader.fieldnames):
                raise KnowledgeError('CSV cần các cột question, answer, keywords; xem tệp mẫu để biết đầy đủ các cột.')
            entries = list(reader)
        elif filename.lower().endswith('.json'):
            document = json.loads(text)
            if not isinstance(document, dict) or document.get('version') != 1:
                raise KnowledgeError('JSON phải có version: 1 và danh sách entries.')
            entries = document.get('entries')
        else:
            raise KnowledgeError('Chỉ hỗ trợ JSON hoặc CSV UTF-8.')
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error) as error:
        raise KnowledgeError('Không đọc được tệp UTF-8. Vui lòng kiểm tra cấu trúc JSON/CSV.') from error
    if not isinstance(entries, list) or not entries:
        raise KnowledgeError('Tệp cần có ít nhất một nội dung.')
    if max_entries is not None and len(entries) > max_entries:
        raise KnowledgeError(f'Mỗi lần nhập trên web tối đa {max_entries} nội dung.')
    result, seen = [], set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise KnowledgeError(f'Dòng {index}: nội dung không hợp lệ.')
        try:
            identifier = str(uuid.UUID(str(entry.get('knowledge_id') or uuid.uuid4())))
        except ValueError as error:
            raise KnowledgeError(f'Dòng {index}: knowledge_id không hợp lệ.') from error
        if identifier in seen:
            raise KnowledgeError(f'Dòng {index}: trùng knowledge_id trong tệp.')
        seen.add(identifier)
        active = entry.get('is_active', False)
        if not isinstance(active, bool):
            active_text = str(active).strip().lower()
            if active_text not in ('true', 'false', '1', '0', ''):
                raise KnowledgeError(f'Dòng {index}: is_active chỉ nhận true/false hoặc 1/0.')
            active = active_text in ('true', '1')
        data = {field: entry.get(field, '') for field in EDIT_FIELDS}
        data.update(category=entry.get('category') or 'Hỗ trợ chung', priority=entry.get('priority') or 0, is_active=active)
        if any(not isinstance(data[field], str) for field in ('question', 'category', 'answer', 'keywords')):
            raise KnowledgeError(f'Dòng {index}: nội dung và từ khóa phải là văn bản.')
        form = KnowledgeForm(data)
        if not form.is_valid():
            errors = '; '.join(f'{field}: {", ".join(values)}' for field, values in form.errors.items())
            raise KnowledgeError(f'Dòng {index}: {errors}')
        result.append({'knowledge_id': identifier, **form.cleaned_data})
    return result


def preview_records(records):
    existing = {str(item.knowledge_id): item for item in ChatFAQ.objects.filter(knowledge_id__in=[item['knowledge_id'] for item in records])}
    rows, revisions = [], {}
    for record in records:
        item = existing.get(record['knowledge_id'])
        revisions[record['knowledge_id']] = fingerprint(item) if item else None
        action = 'Thêm mới' if item is None else ('Không đổi' if equivalent(record_for(item), record) else 'Khác dữ liệu hiện tại')
        if item is None and ChatFAQ.objects.filter(question__iexact=record['question']).exists():
            action = 'Trùng tiêu đề, khác ID — bỏ qua'
        rows.append({'record': record, 'action': action})
    return rows, revisions


def import_records(records, overwrite=False, expected=None):
    counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0}
    with transaction.atomic():
        for record in records:
            item = ChatFAQ.objects.select_for_update().filter(knowledge_id=record['knowledge_id']).first()
            current = fingerprint(item) if item else None
            if expected is not None and current != expected.get(record['knowledge_id']):
                raise KnowledgeError('Dữ liệu đã thay đổi sau khi xem trước. Vui lòng tải tệp lên và kiểm tra lại.')
            if item is None:
                if ChatFAQ.objects.filter(question__iexact=record['question']).exists():
                    counts['skipped'] += 1
                    continue
                ChatFAQ.objects.create(**record)
                counts['created'] += 1
            elif equivalent(record_for(item), record):
                counts['unchanged'] += 1
            elif overwrite:
                for field in EDIT_FIELDS:
                    setattr(item, field, record[field])
                item.save()
                counts['updated'] += 1
            else:
                counts['skipped'] += 1
    return counts


def export_json(queryset):
    return json.dumps({'version': 1, 'entries': [record_for(item) for item in queryset]}, ensure_ascii=False, indent=2)


def export_csv(queryset):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    for item in queryset:
        record = record_for(item)
        record['is_active'] = 'true' if record['is_active'] else 'false'
        for field, value in record.items():
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                record[field] = "'" + value
        writer.writerow(record)
    return '\ufeff' + stream.getvalue()
