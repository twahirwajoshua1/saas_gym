"""
Who may do what, by role.

Services call `require_role(...)` before doing anything, so the rule holds no
matter which screen (or, later, which API) asked.
"""

from django.core.exceptions import PermissionDenied

from apps.gyms.models import Role

# People who run the gym.
MANAGER_ROLES = (Role.OWNER, Role.BRANCH_MANAGER)

# People who deal with members at the desk or on the phone.
FRONT_DESK_ROLES = (Role.OWNER, Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.SALES_REP)

# People who may admit members at the door.
CHECK_IN_ROLES = (Role.OWNER, Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.TRAINER)

OWNER_ONLY = (Role.OWNER,)


def require_role(assignment, allowed_roles) -> None:
    """Stop with "not allowed" unless this staff member has one of the roles."""
    if assignment is None or not assignment.is_active or assignment.role not in allowed_roles:
        raise PermissionDenied("Your role does not allow this.")
