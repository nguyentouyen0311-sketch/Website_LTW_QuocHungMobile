from pathlib import Path
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from PIL import Image, UnidentifiedImageError


@deconstructible
class PrivateReturnStorage(FileSystemStorage):
    @property
    def location(self):
        return str(
            getattr(settings, 'PRIVATE_UPLOAD_ROOT', Path(settings.BASE_DIR) / 'private_uploads')
        )

    def url(self, name):
        raise ValueError('Tệp yêu cầu đổi trả chỉ được đọc qua đường dẫn có phân quyền.')


def validate_return_upload(upload):
    suffix = Path(upload.name).suffix.lower()
    image_types = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}
    if suffix in image_types:
        if upload.size > 10 * 1024 * 1024:
            raise ValidationError('Mỗi ảnh tối đa 10 MB.')
        try:
            with Image.open(BytesIO(upload.read())) as image:
                if image.format != image_types[suffix]:
                    raise ValidationError('Định dạng ảnh không khớp với phần mở rộng.')
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValidationError('Tệp tải lên không phải ảnh hợp lệ.')
        finally:
            upload.seek(0)
        return 'image/jpeg' if suffix in ('.jpg', '.jpeg') else 'image/' + suffix[1:]
    if suffix in ('.mp4', '.webm'):
        if upload.size > 50 * 1024 * 1024:
            raise ValidationError('Mỗi video tối đa 50 MB.')
        header = upload.read(32)
        upload.seek(0)
        if (suffix == '.mp4' and header[4:8] == b'ftyp') or (
            suffix == '.webm' and header.startswith(b'\x1aE\xdf\xa3')
        ):
            return 'video/mp4' if suffix == '.mp4' else 'video/webm'
    raise ValidationError('Chỉ chấp nhận ảnh JPG, PNG, WEBP hoặc video MP4, WEBM hợp lệ.')
