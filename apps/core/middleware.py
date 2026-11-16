from django.urls import reverse

from apps.core import tenant_db


class DatabaseRoleMiddleware:
    """
    Runs every web request as the restricted database role (tenant isolation, lock 2).

    Two exceptions, both deliberate:
      * The status page. It must still answer when the database is down, so it
        cannot depend on the database. It touches no gym data.
      * The platform pages. Signed-in platform staff opening /admin/ or
        /platform/ pages work as the normal database user, so they can see
        every gym. Their changes are recorded in each gym's audit log.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == reverse("core:health"):
            return self.get_response(request)

        user = request.user
        is_platform_admin_page = request.path.startswith((reverse("admin:index"), "/platform/"))
        if is_platform_admin_page and user.is_authenticated and user.is_staff:
            return self.get_response(request)

        person_id = user.person_id if user.is_authenticated else None
        tenant_db.enter_app_role(person_id=person_id)
        try:
            return self.get_response(request)
        finally:
            tenant_db.leave_app_role()
