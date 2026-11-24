"""Makes the current gym and the person's role available to every page template."""

from apps.gyms.permissions import CHECK_IN_ROLES, FRONT_DESK_ROLES, MANAGER_ROLES, OWNER_ONLY


def _nav_section(request) -> str:
    """Which item of the side menu the current page belongs to."""
    match = request.resolver_match
    if match is None:
        return ""
    if match.app_name == "corporate":
        return "companies"
    if match.app_name == "classes":
        return "classes"
    if match.app_name == "frontdesk":
        return "settings" if match.url_name.startswith("tablet") else "checkin"
    if match.url_name == "settings":
        return "settings"
    if match.url_name == "member_register":
        return "register"
    if match.url_name == "my_shift":
        return "shift"
    name = match.url_name or ""
    if match.app_name == "memberapp":
        return "member"
    if match.app_name in ("team", "spreadsheets", "modules"):
        return "settings"
    if match.app_name == "dashboard":
        return "renewals" if name == "renewals" else "overview"
    if match.app_name == "attendance":
        return "member" if name == "member_visits" else "checkin"
    if match.app_name == "billing":
        # Selling and a member's account are reached from the member's page.
        return "member" if name in ("sell", "member_account") else "payment"
    if name.startswith(("service", "package", "branch", "activity")):
        return "settings"
    if name.startswith("member"):
        return "member"
    return "overview"


def current_gym(request):
    assignment = getattr(request, "assignment", None)
    if assignment is None:
        return {}
    return {
        "nav_section": _nav_section(request),
        "gym": assignment.gym,
        "assignment": assignment,
        "role_is_owner": assignment.role in OWNER_ONLY,
        "role_is_manager": assignment.role in MANAGER_ROLES,
        "role_is_front_desk": assignment.role in FRONT_DESK_ROLES,
        "role_can_check_in": assignment.role in CHECK_IN_ROLES,
    }
