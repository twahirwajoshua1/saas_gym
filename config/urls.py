"""
The address book of the application: which web address leads to which code.

Each module keeps its own list of addresses in its own `urls.py`,
and this file simply includes them.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("gym/modules/", include("apps.modules.urls")),
    path("gym/spreadsheets/", include("apps.spreadsheets.urls")),
    path("gym/staff/", include("apps.team.urls")),
    path("gym/", include("apps.dashboard.urls")),
    path("gym/", include("apps.attendance.urls")),
    path("gym/", include("apps.billing.urls")),
    path("gym/", include("apps.members.urls")),
    path("gym/", include("apps.gyms.urls")),
    path("", include("apps.core.urls")),
]
