"""Shared fields and constraints for inventory forms."""

import re

from django import forms
from django.core.exceptions import ValidationError


MAX_INTEGER_LIMIT = 2147483647


class StrictNonNegativeIntegerField(forms.IntegerField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not (val_str.isascii() and re.fullmatch(r'(?:0|[1-9][0-9]*)', val_str)):
                raise ValidationError(
                    self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                    code='invalid',
                )
        elif isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(
                self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                code='invalid',
            )
        return super().to_python(value)
