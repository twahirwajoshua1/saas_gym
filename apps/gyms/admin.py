from django.contrib import admin

from apps.gyms.models import Branch, Gym, StaffAssignment


@admin.register(Gym)
class GymAdmin(admin.ModelAdmin):
    """Gyms are created with the create_gym command so they always get a branch and an owner."""

    list_display = ["name", "is_active", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["public_id", "created_at"]

    def has_add_permission(self, request):
        return False


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ["name", "gym", "is_active"]
    list_filter = ["gym"]
    readonly_fields = ["public_id", "gym", "created_at"]

    def has_add_permission(self, request):
        return False


@admin.register(StaffAssignment)
class StaffAssignmentAdmin(admin.ModelAdmin):
    list_display = ["person", "gym", "role", "branch", "is_active"]
    list_filter = ["gym", "role", "is_active"]
    readonly_fields = ["gym", "person", "created_at"]

    def has_add_permission(self, request):
        return False
