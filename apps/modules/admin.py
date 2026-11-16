from django.contrib import admin

from apps.modules.models import ModuleActivation


@admin.register(ModuleActivation)
class ModuleActivationAdmin(admin.ModelAdmin):
    """
    Read only. Changing modules here would skip the dependency rules and the
    audit log, so use `python manage.py set_module` until the platform admin
    screens are built.
    """

    list_display = ["gym", "module", "is_active", "activated_at", "deactivated_at"]
    list_filter = ["gym", "module", "is_active"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
