from django.db import OperationalError


class ConcurrencyConflict(Exception):
    pass


def _is_sqlite_lock_error(exc: Exception) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    msg = str(exc).lower()
    return 'locked' in msg or 'busy' in msg
