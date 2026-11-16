from django.contrib import admin

from apps.people.models import Person


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["full_name", "phone", "email", "created_at"]
    search_fields = ["full_name", "phone", "email"]
    readonly_fields = ["public_id", "created_at", "updated_at"]
