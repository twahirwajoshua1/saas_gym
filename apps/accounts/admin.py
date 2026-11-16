from django.contrib import admin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Accounts are shown here for inspection. They are created through services."""

    list_display = ["phone", "person", "is_active", "is_staff", "last_login"]
    search_fields = ["phone", "person__full_name"]
    fields = ["person", "phone", "is_active", "is_staff", "last_login", "created_at"]
    readonly_fields = ["person", "phone", "last_login", "created_at"]

    def has_add_permission(self, request):
        return False
