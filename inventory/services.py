"""
Service xử lý nghiệp vụ kho hàng (Inventory Services) — Quốc Hùng Mobile.
Thực thi quy tắc giao dịch nhập hàng (PURCHASE_IN) theo đặc tả Task 05.2:
- Tính nguyên tử (Atomicity): Cập nhật tồn kho và ghi sổ cái trong cùng transaction.atomic.
- Tính lũy thừa / Chống lặp (Idempotency): Sử dụng server-issued signed token gắn với UUID request_key.
  Khi nhận lại cùng token với cùng thông số, trả về kết quả ban đầu mà không tăng tồn lần 2.
- Kiểm soát tương tranh SQLite (CAS / Conditional Update): Chống lost-update bằng so khớp tồn trước khi cập nhật.
- Phân quyền nhiều tầng: Kiểm tra vai trò nhân viên kho / admin cả ở view và bên trong service.
"""
import time
import uuid
import random
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from accounts.permissions import is_inventory_staff
from inventory.models import (
    Inventory,
    InventoryTransaction,
    InventoryAdjustment,
    ReturnedStock,
    ReturnedStockCondition,
)

MAX_INTEGER_LIMIT = 2147483647
SALT_RECEIVE_TOKEN = 'qhm.inventory.receive'
SALT_ADJUSTMENT_TOKEN = 'qhm.inventory.adjust'


class ConcurrencyConflict(Exception):
    """Ngoại lệ nội bộ khi phát hiện xung đột cập nhật tồn kho đồng thời (CAS mismatch)."""
    pass


def generate_receiving_token(inventory_id: int, actor_id: int) -> tuple[str, uuid.UUID]:
    """
    Sinh token xác thực yêu cầu nhập hàng có chữ ký bảo mật từ máy chủ.
    - Sinh mã UUID ngẫu nhiên làm request_key.
    - Ràng buộc chặt chẽ với ID bản ghi tồn kho (inventory_id) và người thực hiện (actor_id).
    - Trả về tuple: (signed_token_str, request_key_uuid).
    """
    request_key = uuid.uuid4()
    payload = {
        'request_key': str(request_key),
        'inventory_id': inventory_id,
        'actor_id': actor_id,
    }
    signed_token = signing.dumps(payload, salt=SALT_RECEIVE_TOKEN)
    return signed_token, request_key


def verify_receiving_token(token: str, inventory_id: int, actor_id: int) -> uuid.UUID:
    """
    Xác minh chữ ký và tính hợp lệ của token yêu cầu nhập hàng.
    - Giải mã token có chữ ký bằng SECRET_KEY và salt bảo mật.
    - Kiểm tra thời hạn hiệu lực (mặc định tối đa 24 giờ / 86400 giây).
    - Đối soát chính xác inventory_id và actor_id để chống giả mạo hoặc tráo đổi biểu mẫu.
    - Trả về đối tượng UUID request_key nếu hợp lệ; ném ValidationError nếu không hợp lệ.
    """
    if not token or not isinstance(token, str):
        raise ValidationError('Mã xác thực yêu cầu nhập hàng không được để trống.', code='invalid_token')

    try:
        payload = signing.loads(token, salt=SALT_RECEIVE_TOKEN, max_age=86400)
    except signing.SignatureExpired:
        raise ValidationError('Phiên làm việc nhập hàng đã hết hạn (quá 24 giờ). Vui lòng tải lại trang.', code='token_expired')
    except (signing.BadSignature, Exception):
        raise ValidationError('Mã xác thực yêu cầu nhập hàng không hợp lệ hoặc đã bị thay đổi.', code='invalid_token')

    if payload.get('inventory_id') != inventory_id:
        raise ValidationError('Mã xác thực không khớp với bản ghi tồn kho đang thao tác.', code='invalid_token_binding')

    if payload.get('actor_id') != actor_id:
        raise ValidationError('Mã xác thực không khớp với tài khoản người thực hiện.', code='invalid_token_binding')

    raw_key = payload.get('request_key')
    try:
        return uuid.UUID(raw_key)
    except (ValueError, TypeError):
        raise ValidationError('Mã định danh yêu cầu trong token không đúng định dạng UUID.', code='invalid_token')


def receive_purchase(
    inventory_id: int,
    quantity: int,
    request_token: str,
    actor,
    max_retries: int = 5
) -> tuple[InventoryTransaction, bool]:
    """
    Thực hiện giao dịch Nhập hàng (PURCHASE_IN) cho một bản ghi tồn kho.

    Tham số:
    - inventory_id: ID bản ghi tồn kho cần nhập thêm hàng.
    - quantity: Số lượng nhập (phải là số nguyên dương > 0).
    - request_token: Chuỗi token đã được máy chủ ký phát hành.
    - actor: Đối tượng User thực hiện thao tác (bắt buộc phải là nhân viên kho / Admin đang hoạt động).
    - max_retries: Số lần thử lại tối đa khi gặp xung đột tương tranh SQLite.

    Trả về:
    - tuple: (tx: InventoryTransaction, is_replay: bool)
      * is_replay=False: Giao dịch nhập mới thành công, tồn kho đã tăng.
      * is_replay=True: Giao dịch lặp lại với cùng token và dữ liệu, trả về kết quả cũ mà không tăng tồn lần 2.

    Quy trình an toàn:
    1. Kiểm tra phân quyền nghiệp vụ người thực hiện (is_inventory_staff).
    2. Thẩm định số lượng nhập (1 <= quantity <= 2,147,483,647).
    3. Xác minh chữ ký token, trích xuất request_key.
    4. Kiểm tra chống ghi lặp (Idempotency):
       - Nếu request_key đã tồn tại và khớp toàn bộ thông số: Trả về bản ghi đã ghi nhận (is_replay=True).
       - Nếu request_key đã tồn tại nhưng sai lệch thông số: Từ chối với ValidationError.
    5. Cập nhật có điều kiện (CAS) kết hợp transaction.atomic:
       - Kiểm tra chống tràn số nguyên: physical + quantity <= 2,147,483,647.
       - Cập nhật tồn thực tế `physical_sellable` theo điều kiện giá trị trước không đổi.
       - Ghi một dòng lịch sử `InventoryTransaction` loại PURCHASE_IN cùng transaction.
       - Giữ nguyên 100% `reserved` và `min_threshold`.
    """
    # 1. Phân quyền nghiệp vụ
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Người thực hiện thao tác chưa được xác thực hoặc tài khoản bị vô hiệu hóa.')

    if not is_inventory_staff(actor):
        raise PermissionDenied('Bạn không có quyền thực hiện thao tác nhập hàng vào kho.')

    # 2. Thẩm định số lượng
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValidationError({'quantity': 'Số lượng nhập phải là một số nguyên hợp lệ.'})
    q = quantity

    if q <= 0:
        raise ValidationError({'quantity': 'Số lượng nhập phải là số nguyên dương lớn hơn 0.'})

    if q > MAX_INTEGER_LIMIT:
        raise ValidationError({'quantity': f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.'})

    # 3. Xác minh chữ ký token
    request_key = verify_receiving_token(request_token, inventory_id=inventory_id, actor_id=actor.id)

    # 4. Kiểm tra sự tồn tại của Inventory
    try:
        Inventory.objects.get(id=inventory_id)
    except Inventory.DoesNotExist:
        raise ValidationError('Bản ghi tồn kho cần nhập hàng không tồn tại trong hệ thống.')

    # 5. Vòng lặp giao dịch nguyên tử & CAS tương tranh
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                # 5.1. Kiểm tra idempotency ngay đầu atomic block
                existing_tx = (
                    InventoryTransaction.objects
                    .select_related('inventory', 'performed_by')
                    .filter(request_key=request_key)
                    .first()
                )
                if existing_tx:
                    if (
                        existing_tx.inventory_id == inventory_id
                        and existing_tx.performed_by_id == actor.id
                        and existing_tx.quantity_delta == q
                    ):
                        return existing_tx, True
                    else:
                        raise ValidationError(
                            'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác hoặc với thông số khác.',
                            code='idempotency_payload_conflict'
                        )

                # 5.2. Đọc tồn kho hiện thời từ CSDL
                current_inv = (
                    Inventory.objects
                    .filter(id=inventory_id)
                    .values('physical_sellable', 'reserved', 'min_threshold')
                    .first()
                )
                if not current_inv:
                    raise ValidationError('Bản ghi tồn kho không tồn tại.')

                current_physical = current_inv['physical_sellable']
                new_physical = current_physical + q

                if new_physical > MAX_INTEGER_LIMIT:
                    raise ValidationError(
                        f'Số lượng nhập ({q:,}) làm tồn thực tế vượt quá giới hạn tối đa cho phép ({MAX_INTEGER_LIMIT:,}).',
                        code='balance_overflow'
                    )

                # 5.3. Conditional CAS Update: Chỉ cập nhật nếu physical_sellable chưa bị luồng khác sửa
                rows_updated = (
                    Inventory.objects
                    .filter(id=inventory_id, physical_sellable=current_physical)
                    .update(
                        physical_sellable=new_physical,
                        updated_at=timezone.now(),
                    )
                )

                if rows_updated == 0:
                    # Đã có giao dịch đồng thời cập nhật tồn kho trong tích tắc vừa qua
                    raise ConcurrencyConflict('Tồn kho đã thay đổi bởi một giao dịch khác.')

                # 5.4. Ghi bản ghi sổ cái biến động kho trong cùng atomic block
                tx = InventoryTransaction.objects.create(
                    inventory_id=inventory_id,
                    transaction_type=InventoryTransaction.TYPE_PURCHASE_IN,
                    quantity_delta=q,
                    physical_before=current_physical,
                    physical_after=new_physical,
                    performed_by=actor,
                    request_key=request_key,
                )

                return tx, False

        except ConcurrencyConflict:
            if attempt == max_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có giao dịch kho đồng thời khác. Vui lòng thử lại sau giây lát.'
                )
            time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
            continue

        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        'Cơ sở dữ liệu kho đang bận xử lý giao dịch khác. Vui lòng thử lại sau.'
                    )
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise

        except IntegrityError as exc:
            # Xử lý trường hợp race condition trên unique constraint của request_key
            existing_tx = (
                InventoryTransaction.objects
                .select_related('inventory', 'performed_by')
                .filter(request_key=request_key)
                .first()
            )
            if existing_tx:
                if (
                    existing_tx.inventory_id == inventory_id
                    and existing_tx.performed_by_id == actor.id
                    and existing_tx.quantity_delta == q
                ):
                    return existing_tx, True
                else:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError('Cơ sở dữ liệu kho đang bận xử lý. Vui lòng thử lại sau.')
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise



def generate_adjustment_token(inventory: Inventory, actor_id: int) -> tuple[str, uuid.UUID]:
    """
    Sinh token xác thực yêu cầu kiểm kê và điều chỉnh tồn kho có chữ ký bảo mật từ máy chủ (UI-32).
    - Namespace thao tác riêng biệt: 'operation': 'ADJUSTMENT' và salt riêng biệt SALT_ADJUSTMENT_TOKEN.
    - Ràng buộc chặt chẽ với:
      * ID bản ghi tồn kho (inventory_id)
      * Người thực hiện (actor_id)
      * Snapshot phiên bản tồn kho hiện hành tại thời điểm người dùng xem form:
        tồn thực tế (snapshot_physical), tồn đã giữ (snapshot_reserved), thời điểm cập nhật (snapshot_updated_at).
    - Trả về tuple: (signed_token_str, request_key_uuid).
    """
    request_key = uuid.uuid4()
    updated_at_str = str(inventory.updated_at.isoformat()) if inventory.updated_at else ''
    payload = {
        'operation': 'ADJUSTMENT',
        'request_key': str(request_key),
        'inventory_id': inventory.id,
        'actor_id': actor_id,
        'snapshot_physical': inventory.physical_sellable,
        'snapshot_reserved': inventory.reserved,
        'snapshot_updated_at': updated_at_str,
    }
    signed_token = signing.dumps(payload, salt=SALT_ADJUSTMENT_TOKEN)
    return signed_token, request_key


def verify_adjustment_token(token: str, inventory_id: int, actor_id: int) -> dict:
    """
    Xác minh chữ ký và tính hợp lệ của token yêu cầu điều chỉnh kiểm kê kho.
    - Giải mã token có chữ ký bằng SECRET_KEY và salt bảo mật SALT_ADJUSTMENT_TOKEN.
    - Kiểm tra thời hạn hiệu lực (mặc định tối đa 24 giờ / 86400 giây).
    - Kiểm tra đúng operation == 'ADJUSTMENT' (chống dùng lẫn token nhập hàng cho kiểm kê).
    - Đối soát chính xác inventory_id và actor_id để chống giả mạo hoặc tráo đổi biểu mẫu.
    - Trích xuất và kiểm tra request_key là UUID hợp lệ.
    - Trả về payload dict chứa thông tin snapshot và request_key; ném ValidationError nếu không hợp lệ.
    """
    if not token or not isinstance(token, str):
        raise ValidationError('Mã xác thực yêu cầu kiểm kê kho không được để trống.', code='invalid_token')

    try:
        payload = signing.loads(token, salt=SALT_ADJUSTMENT_TOKEN, max_age=86400)
    except signing.SignatureExpired:
        raise ValidationError('Phiên làm việc kiểm kê kho đã hết hạn (quá 24 giờ). Vui lòng tải lại trang.', code='token_expired')
    except (signing.BadSignature, Exception):
        raise ValidationError('Mã xác thực yêu cầu kiểm kê không hợp lệ hoặc đã bị thay đổi.', code='invalid_token')

    if payload.get('operation') != 'ADJUSTMENT':
        raise ValidationError('Mã xác thực không thuộc nghiệp vụ kiểm kê điều chỉnh kho.', code='invalid_token_operation')

    if payload.get('inventory_id') != inventory_id:
        raise ValidationError('Mã xác thực không khớp với bản ghi tồn kho đang thao tác.', code='invalid_token_binding')

    if payload.get('actor_id') != actor_id:
        raise ValidationError('Mã xác thực không khớp với tài khoản người thực hiện.', code='invalid_token_binding')

    raw_key = payload.get('request_key')
    try:
        payload['request_key_uuid'] = uuid.UUID(raw_key)
    except (ValueError, TypeError):
        raise ValidationError('Mã định danh yêu cầu trong token không đúng định dạng UUID.', code='invalid_token')

    return payload


def adjust_inventory(
    inventory_id: int,
    counted_quantity: int,
    reason: str,
    request_token: str,
    actor,
    max_retries: int = 5,
) -> tuple[InventoryAdjustment, bool]:
    """
    Thực hiện nghiệp vụ Kiểm kê và Điều chỉnh tồn kho (ADJUSTMENT) — Task 05.3.

    Tham số:
    - inventory_id: ID bản ghi tồn kho cần điều chỉnh.
    - counted_quantity: Số lượng đếm thực tế tuyệt đối trong kho (số nguyên >= 0).
    - reason: Chuỗi văn bản nêu rõ nguyên nhân chênh lệch (bắt buộc, không để trống).
    - request_token: Chuỗi token đã được máy chủ ký phát hành gắn với snapshot đã xem.
    - actor: Đối tượng User thực hiện thao tác (bắt buộc là nhân viên kho / Admin đang hoạt động).
    - max_retries: Số lần thử lại tối đa khi gặp xung đột tương tranh SQLite.

    Trả về:
    - tuple: (adj: InventoryAdjustment, is_replay: bool)
      * is_replay=False: Điều chỉnh mới thành công, tồn kho và sổ cái đã cập nhật.
      * is_replay=True: Yêu cầu lặp lại hợp lệ với cùng token và dữ liệu, trả về kết quả cũ mà không ghi lặp.
    """
    # 1. Phân quyền nghiệp vụ tại tầng service
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('Người thực hiện thao tác chưa được xác thực hoặc tài khoản bị vô hiệu hóa.')

    if not is_inventory_staff(actor):
        raise PermissionDenied('Bạn không có quyền thực hiện thao tác kiểm kê điều chỉnh tồn kho.')

    # 2. Thẩm định số lượng kiểm kê
    if isinstance(counted_quantity, bool) or not isinstance(counted_quantity, int):
        raise ValidationError({'counted_quantity': 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'})
    q = counted_quantity

    if q < 0:
        raise ValidationError({'counted_quantity': 'Số lượng kiểm kê không được là số âm (phải >= 0).'})

    if q > MAX_INTEGER_LIMIT:
        raise ValidationError({'counted_quantity': f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.'})

    # 3. Thẩm định lý do điều chỉnh
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({'reason': 'Lý do điều chỉnh không được để trống hoặc chỉ chứa khoảng trắng.'})
    canonical_reason = reason.strip()

    # 4. Xác minh chữ ký token và trích xuất snapshot phiên bản đã xem
    token_payload = verify_adjustment_token(request_token, inventory_id=inventory_id, actor_id=actor.id)
    request_key = token_payload['request_key_uuid']
    snapshot_physical = token_payload.get('snapshot_physical')
    snapshot_reserved = token_payload.get('snapshot_reserved')
    snapshot_updated_at = token_payload.get('snapshot_updated_at')

    # 5. Kiểm tra tồn tại của Inventory
    try:
        Inventory.objects.get(id=inventory_id)
    except Inventory.DoesNotExist:
        raise ValidationError('Bản ghi tồn kho cần điều chỉnh không tồn tại trong hệ thống.')

    # 6. Vòng lặp giao dịch nguyên tử & CAS tương tranh
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                # 6.1. Kiểm tra Idempotency / Replay ngay đầu atomic block
                existing_adj = (
                    InventoryAdjustment.objects
                    .select_related('inventory', 'adjusted_by', 'transaction')
                    .filter(transaction__request_key=request_key)
                    .first()
                )
                if existing_adj:
                    if (
                        existing_adj.inventory_id == inventory_id
                        and existing_adj.adjusted_by_id == actor.id
                        and existing_adj.new_quantity == q
                        and existing_adj.reason == canonical_reason
                        and existing_adj.transaction.transaction_type == InventoryTransaction.TYPE_ADJUSTMENT
                    ):
                        return existing_adj, True
                    else:
                        raise ValidationError(
                            'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác hoặc với thông số khác.',
                            code='idempotency_payload_conflict'
                        )

                # Chống va chạm khóa yêu cầu chéo nghiệp vụ (ví dụ request_key đã dùng cho PURCHASE_IN)
                other_tx = InventoryTransaction.objects.filter(request_key=request_key).first()
                if other_tx:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một loại giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

                # 6.2. Đọc tồn kho hiện thời từ CSDL
                current_inv = (
                    Inventory.objects
                    .filter(id=inventory_id)
                    .values('physical_sellable', 'reserved', 'min_threshold', 'updated_at')
                    .first()
                )
                if not current_inv:
                    raise ValidationError('Bản ghi tồn kho không tồn tại.')

                current_physical = current_inv['physical_sellable']
                current_reserved = current_inv['reserved']
                current_updated_at = current_inv['updated_at']
                current_updated_at_str = str(current_updated_at.isoformat()) if current_updated_at else ''

                # 6.3. Kiểm tra phiên bản tồn kho (Stale Snapshot Detection)
                # Nếu tồn thực tế, tồn đã giữ hoặc thời điểm cập nhật đã đổi kể từ lúc operator xem form:
                if (
                    current_physical != snapshot_physical
                    or current_reserved != snapshot_reserved
                    or current_updated_at_str != snapshot_updated_at
                ):
                    raise ValidationError(
                        'Tồn kho đã thay đổi so với thời điểm xem trước kiểm kê. Vui lòng kiểm tra lại số liệu mới nhất.',
                        code='stale_snapshot'
                    )

                # 6.4. Kiểm tra không có chênh lệch (No difference)
                if q == current_physical:
                    raise ValidationError(
                        'Số lượng kiểm kê trùng khớp với tồn thực tế hiện tại (không có chênh lệch). Không phát sinh điều chỉnh kho.',
                        code='no_difference'
                    )

                # 6.5. Kiểm tra sàn giữ hàng (Reserved floor)
                if q < current_reserved:
                    raise ValidationError(
                        f'Số lượng kiểm kê ({q:,}) không được nhỏ hơn số lượng hàng đang giữ cho đơn hàng ({current_reserved:,}).',
                        code='below_reserved'
                    )

                # 6.6. Conditional CAS Update: Chỉ cập nhật nếu physical và reserved chưa bị luồng khác sửa
                rows_updated = (
                    Inventory.objects
                    .filter(id=inventory_id, physical_sellable=current_physical, reserved=current_reserved)
                    .update(
                        physical_sellable=q,
                        updated_at=timezone.now(),
                    )
                )

                if rows_updated == 0:
                    raise ConcurrencyConflict('Tồn kho đã thay đổi bởi một giao dịch khác trong quá trình xử lý.')

                # 6.7. Ghi nhận sổ cái biến động kho InventoryTransaction (loại ADJUSTMENT)
                delta = q - current_physical
                tx = InventoryTransaction.objects.create(
                    inventory_id=inventory_id,
                    transaction_type=InventoryTransaction.TYPE_ADJUSTMENT,
                    quantity_delta=delta,
                    physical_before=current_physical,
                    physical_after=q,
                    performed_by=actor,
                    request_key=request_key,
                )

                # 6.8. Ghi nhận bản ghi kiểm kê InventoryAdjustment liên kết 1-1 với tx
                adj = InventoryAdjustment.objects.create(
                    inventory_id=inventory_id,
                    old_quantity=current_physical,
                    new_quantity=q,
                    reason=canonical_reason,
                    adjusted_by=actor,
                    transaction=tx,
                )

                return adj, False

        except ConcurrencyConflict:
            if attempt == max_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có giao dịch kho đồng thời khác. Vui lòng thử lại sau giây lát.'
                )
            time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.04))
            continue

        except OperationalError as exc:
            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError(
                        'Cơ sở dữ liệu kho đang bận xử lý giao dịch khác. Vui lòng thử lại sau.'
                    )
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise

        except IntegrityError as exc:
            # Xử lý trường hợp race condition trên unique constraint của request_key
            existing_adj = (
                InventoryAdjustment.objects
                .select_related('inventory', 'adjusted_by', 'transaction')
                .filter(transaction__request_key=request_key)
                .first()
            )
            if existing_adj:
                if (
                    existing_adj.inventory_id == inventory_id
                    and existing_adj.adjusted_by_id == actor.id
                    and existing_adj.new_quantity == q
                    and existing_adj.reason == canonical_reason
                    and existing_adj.transaction.transaction_type == InventoryTransaction.TYPE_ADJUSTMENT
                ):
                    return existing_adj, True
                else:
                    raise ValidationError(
                        'Khóa yêu cầu này đã được sử dụng cho một giao dịch khác.',
                        code='idempotency_payload_conflict'
                    )

            err_msg = str(exc).lower()
            if 'busy' in err_msg or 'locked' in err_msg:
                if attempt == max_retries - 1:
                    raise ValidationError('Cơ sở dữ liệu kho đang bận xử lý. Vui lòng thử lại sau.')
                time.sleep(0.05 * (attempt + 1) + random.uniform(0.02, 0.06))
                continue
            raise


def confirm_return_receipt(
    return_request_id: int,
    actor,
) -> ReturnedStock:
    """
    Nhân viên kho (Inventory Staff / Admin) xác nhận đã nhận hàng đổi/trả về kho (Requirement 1 & 4).
    - Phân quyền: Inventory Staff / Admin (Người khác: PermissionDenied).
    - Yêu cầu đổi trả phải ở trạng thái APPROVED.
    - Tạo bản ghi ReturnedStock với condition = WAITING_INSPECTION.
    - Cập nhật ReturnRequest sang RECEIVED.
    - TUYỆT ĐỐI KHÔNG làm tăng tồn khả dụng hoặc tồn thực tế (physical_sellable bất biến).
    """
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Yêu cầu đăng nhập tài khoản.")
    if not is_inventory_staff(actor):
        raise PermissionDenied("Chỉ Nhân viên kho hoặc Quản trị viên mới có quyền tiếp nhận hàng đổi trả.")

    from orders.models import ReturnRequest, ReturnRequestStatus
    try:
        return_req = ReturnRequest.objects.select_related('order_item', 'order_item__variant').get(pk=return_request_id)
    except ReturnRequest.DoesNotExist:
        raise ValidationError("Yêu cầu đổi/trả không tồn tại.", code='not_found')

    if return_req.status != ReturnRequestStatus.APPROVED:
        # Nếu đã nhận rồi -> Idempotent trả về bản ghi hiện có
        if return_req.status in (ReturnRequestStatus.RECEIVED, ReturnRequestStatus.COMPLETED):
            existing_rs = getattr(return_req, 'returned_stock', None)
            if existing_rs:
                return existing_rs
        raise ValidationError(
            f"Yêu cầu đổi/trả đang ở trạng thái '{return_req.get_status_display()}', chỉ yêu cầu đã được duyệt (APPROVED) mới được nhận vào kho.",
            code='invalid_status'
        )

    with transaction.atomic():
        now = timezone.now()
        returned_stock, created = ReturnedStock.objects.get_or_create(
            return_request=return_req,
            defaults={
                'variant': return_req.order_item.variant,
                'quantity': return_req.quantity,
                'condition': ReturnedStockCondition.WAITING_INSPECTION,
                'received_by': actor,
                'received_at': now,
            }
        )
        if not created and not returned_stock.received_at:
            returned_stock.received_by = actor
            returned_stock.received_at = now
            returned_stock.save(update_fields=['received_by', 'received_at'])

        return_req.status = ReturnRequestStatus.RECEIVED
        return_req.save(update_fields=['status', 'updated_at'])

    return returned_stock


def record_returned_stock_condition(
    returned_stock_id: int,
    condition: str,
    inspection_notes: str,
    actor,
) -> ReturnedStock:
    """
    Nhân viên kho (Inventory Staff / Admin) kiểm tra chất lượng và phân loại tình trạng hàng trả (Requirement 1 & 2):
    - WAITING_INSPECTION: Chờ kiểm định.
    - ELIGIBLE_FOR_RESALE: Đủ điều kiện bán lại.
    - NOT_ELIGIBLE_FOR_RESALE: Không đủ điều kiện bán lại (hỏng, lỗi, không thể bán lại).
    - Quy tắc: Cả WAITING_INSPECTION và NOT_ELIGIBLE_FOR_RESALE đều KHÔNG làm tăng tồn khả dụng.
      Chỉ ghi nhận phân loại, physical_sellable bất biến, không ghi sổ cái InventoryTransaction.
    """
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Yêu cầu đăng nhập tài khoản.")
    if not is_inventory_staff(actor):
        raise PermissionDenied("Chỉ Nhân viên kho hoặc Quản trị viên mới có quyền kiểm định tình trạng hàng trả.")

    if condition not in ReturnedStockCondition.values:
        raise ValidationError(f"Phân loại tình trạng '{condition}' không hợp lệ.", code='invalid_condition')

    try:
        returned_stock = ReturnedStock.objects.select_related('return_request').get(pk=returned_stock_id)
    except ReturnedStock.DoesNotExist:
        raise ValidationError("Bản ghi hàng đổi trả không tồn tại.", code='not_found')

    if returned_stock.is_restocked:
        raise ValidationError("Sản phẩm trả này đã được nhập lại vào kho bán trước đó, không thể thay đổi tình trạng kiểm định.", code='already_restocked')

    returned_stock.condition = condition
    returned_stock.inspection_notes = inspection_notes.strip() if inspection_notes else ''
    returned_stock.inspected_by = actor
    returned_stock.inspected_at = timezone.now()
    returned_stock.save(update_fields=['condition', 'inspection_notes', 'inspected_by', 'inspected_at'])

    return returned_stock


def restock_eligible_returned_product(
    returned_stock_id: int,
    actor,
    max_retries: int = 5,
) -> tuple[InventoryTransaction, ReturnedStock]:
    """
    Nhân viên kho (Inventory Staff / Admin) nhập hàng đổi trả đủ điều kiện vào tồn kho bán được (Requirement 2 & 6).
    - Phân quyền: Inventory Staff / Admin (Người khác: PermissionDenied).
    - Điều kiện: condition == ELIGIBLE_FOR_RESALE.
      Từ chối nếu WAITING_INSPECTION hoặc NOT_ELIGIBLE_FOR_RESALE.
    - Tăng physical_sellable an toàn qua CAS conditional update.
    - Tạo bản ghi sổ cái InventoryTransaction loại TYPE_RETURN_RESTOCK (+quantity).
    - Đánh dấu returned_stock.is_restocked = True, restocked_at = now, transaction = tx.
    - Cập nhật return_request.status sang COMPLETED.
    - Idempotent: Nếu đã restocked, trả về bản ghi giao dịch cũ mà không tăng tồn lần 2.
    """
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Yêu cầu đăng nhập tài khoản.")
    if not is_inventory_staff(actor):
        raise PermissionDenied("Chỉ Nhân viên kho hoặc Quản trị viên mới có quyền nhập hàng trả vào kho bán.")

    try:
        returned_stock = ReturnedStock.objects.select_related('return_request', 'variant', 'transaction').get(pk=returned_stock_id)
    except ReturnedStock.DoesNotExist:
        raise ValidationError("Bản ghi hàng đổi trả không tồn tại.", code='not_found')

    if returned_stock.condition != ReturnedStockCondition.ELIGIBLE_FOR_RESALE:
        raise ValidationError(
            f"Chỉ hàng đổi trả có tình trạng 'Đủ điều kiện bán lại' mới được phép nhập lại kho bán. Hiện tại: '{returned_stock.get_condition_display()}'.",
            code='not_eligible_for_resale'
        )

    if returned_stock.is_restocked:
        return returned_stock.transaction, returned_stock

    qty = returned_stock.quantity
    variant = returned_stock.variant

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                # Kiểm tra lại idempotency trong atomic block
                rs_current = ReturnedStock.objects.select_for_update().select_related('transaction', 'return_request').get(pk=returned_stock.pk)
                if rs_current.is_restocked:
                    return rs_current.transaction, rs_current

                inv = Inventory.objects.select_for_update().filter(variant=variant).first()
                if not inv:
                    raise ValidationError(f"Không tìm thấy bản ghi tồn kho cho biến thể SKU {variant.sku}.", code='missing_inventory')

                current_physical = inv.physical_sellable
                new_physical = current_physical + qty

                if new_physical > MAX_INTEGER_LIMIT:
                    raise ValidationError(f"Số lượng sau nhập vượt quá giới hạn cho phép ({MAX_INTEGER_LIMIT:,}).", code='overflow')

                event_now = timezone.now()
                # CAS update
                rows = Inventory.objects.filter(
                    pk=inv.pk,
                    physical_sellable=current_physical,
                ).update(
                    physical_sellable=new_physical,
                    updated_at=event_now,
                )
                if rows != 1:
                    raise ConcurrencyConflict("Xung đột tương tranh tồn kho khi nhập lại hàng trả.")

                # Ghi sổ cái biến động kho
                tx = InventoryTransaction.objects.create(
                    inventory=inv,
                    transaction_type=InventoryTransaction.TYPE_RETURN_RESTOCK,
                    quantity_delta=qty,
                    physical_before=current_physical,
                    physical_after=new_physical,
                    performed_by=actor,
                    return_request=rs_current.return_request,
                    request_key=uuid.uuid4(),
                )

                rs_current.is_restocked = True
                rs_current.restocked_at = event_now
                rs_current.restocked_by = actor
                rs_current.transaction = tx
                rs_current.save(update_fields=['is_restocked', 'restocked_at', 'restocked_by', 'transaction'])

                from orders.models import ReturnRequestStatus
                rs_current.return_request.status = ReturnRequestStatus.COMPLETED
                rs_current.return_request.save(update_fields=['status', 'updated_at'])

                return tx, rs_current

        except ConcurrencyConflict:
            if attempt == max_retries - 1:
                raise ValidationError("Hệ thống đang bận xử lý tồn kho đồng thời. Vui lòng thử lại sau.", code='concurrency_exhausted')
            time.sleep(0.03 * (attempt + 1) + random.uniform(0.01, 0.03))
            continue

