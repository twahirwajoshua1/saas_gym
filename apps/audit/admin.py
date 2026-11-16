from django.contrib import admin

from apps.audit.models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Read only. The database also refuses any change, even from here."""

    list_display = ["created_at", "gym", "actor", "action", "summary"]
    list_filter = ["gym", "action"]
    search_fields = ["summary", "action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
