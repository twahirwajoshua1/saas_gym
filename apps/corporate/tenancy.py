"""
Deciding which company a request acts for: the company version of
apps/gyms/tenancy.py. Every company screen is wrapped in `@company_required`.

The company chosen is kept in the session only as a hint and checked against
the database on every request, so removing an officer takes effect at once.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from apps.core import tenant_db
from apps.corporate import services

SESSION_KEY = "current_company"


def select_company(request, officer) -> None:
    request.session[SESSION_KEY] = str(officer.company.public_id)


def company_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        company_id = request.session.get(SESSION_KEY)
        officer = None
        if company_id:
            officer = services.find_officer_role(
                person=request.user.person, company_public_id=company_id
            )
        if officer is None:
            request.session.pop(SESSION_KEY, None)
            return redirect("corporate:start")
        request.officer = officer
        request.company = officer.company
        tenant_db.set_current_company(officer.company_id)
        return view(request, *args, **kwargs)

    return wrapped
