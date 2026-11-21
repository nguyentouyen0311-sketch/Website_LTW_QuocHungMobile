from django import forms

from .services import MAX_MESSAGE_LENGTH
from .models import ChatFAQ
from .services import normalize_text


class ChatContentForm(forms.Form):

    content = forms.CharField(
        label='Nội dung',
        max_length=MAX_MESSAGE_LENGTH,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Nhập câu hỏi của bạn...',
            'maxlength': MAX_MESSAGE_LENGTH,
        }),
        error_messages={
            'required': 'Vui lòng nhập nội dung tin nhắn.',
            'max_length': f'Tin nhắn tối đa {MAX_MESSAGE_LENGTH} ký tự.',
        },
    )


class KnowledgeForm(forms.ModelForm):
    question = forms.CharField(label='Tiêu đề / câu hỏi', max_length=250)
    answer = forms.CharField(label='Nội dung trả lời', max_length=10000, widget=forms.Textarea(attrs={'rows': 6}))
    keywords = forms.CharField(label='Từ khóa kích hoạt', max_length=1000, widget=forms.Textarea(attrs={'rows': 3}), help_text='Phân cách bằng dấu phẩy. Ví dụ: cod, trả tiền khi nhận hàng, thanh toán khi nhận.')
    priority = forms.IntegerField(label='Mức ưu tiên', min_value=0, max_value=100, initial=0, help_text='Từ 0 đến 100. Nếu nhiều nội dung cùng khớp, mức cao hơn được ưu tiên; cùng mức sẽ hỏi khách chọn chủ đề.')
    is_active = forms.BooleanField(label='Công bố cho khách hàng', required=False, initial=False)

    class Meta:
        model = ChatFAQ
        fields = ['question', 'category', 'answer', 'keywords', 'priority', 'is_active']

    def clean_keywords(self):
        values, seen = [], set()
        for raw in self.cleaned_data['keywords'].replace('\n', ',').split(','):
            value = ' '.join(raw.split())
            key = normalize_text(value)
            if len(key) >= 2 and key not in seen:
                values.append(value)
                seen.add(key)
        if not values:
            raise forms.ValidationError('Cần ít nhất một từ khóa từ 2 ký tự.')
        return ', '.join(values)


class KnowledgeImportForm(forms.Form):
    file = forms.FileField(label='Tệp JSON hoặc CSV', widget=forms.ClearableFileInput(attrs={'accept': '.json,.csv'}))

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > 2 * 1024 * 1024:
            raise forms.ValidationError('Tệp tối đa 2 MB.')
        if not upload.name.lower().endswith(('.json', '.csv')):
            raise forms.ValidationError('Chỉ hỗ trợ JSON hoặc CSV UTF-8.')
        return upload
