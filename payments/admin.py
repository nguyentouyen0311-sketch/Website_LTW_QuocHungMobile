from django.contrib import admin
from payments.models import CODCollection


@admin.register(CODCollection)
class CODCollectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'payment', 'amount', 'reference', 'collected_by', 'collected_at')
    search_fields = ('payment__order__order_code', 'reference', 'collected_by__username')
    readonly_fields = ('payment', 'amount', 'reference', 'collected_by', 'collected_at', 'note')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

