"""
Which branch a desk is at.

Where somebody works is part of their job, not a setting they pick each
morning: it is decided when they are added to the staff. A receptionist is
therefore never asked, and cannot change it. Only an owner or a branch
manager, who are not tied to one branch, choose which branch they are looking
at, and that choice is kept in their session.

Several modules need to read this (the desk, registering a member, the massage
diary), so the key and the lookup live here, below all of them.
"""

from apps.gyms.models import Branch
from apps.gyms.permissions import MANAGER_ROLES

DESK_BRANCH = "desk_branch"


def may_choose_branch(request) -> bool:
    """Owners and branch managers move between branches; nobody else does."""
    assignment = getattr(request, "assignment", None)
    return (
        assignment is not None and assignment.branch_id is None and assignment.role in MANAGER_ROLES
    )


def desk_branch(request) -> Branch | None:
    """
    The branch this desk is at.

    For most staff it is simply the branch they were given. For an owner or a
    branch manager with no branch of their own it is the one they chose, or the
    gym's only branch. None means the page cannot know, and should say so
    rather than guess: guessing would put money and visits on the wrong branch.
    """
    assignment = getattr(request, "assignment", None)
    if assignment is None:
        return None
    if assignment.branch_id is not None:
        return assignment.branch
    branches = Branch.objects.for_gym(request.gym).filter(is_active=True)
    only = list(branches[:2])
    if len(only) == 1:
        return only[0]
    if not may_choose_branch(request):
        return None
    chosen_id = request.session.get(DESK_BRANCH)
    if chosen_id:
        return branches.filter(public_id=chosen_id).first()
    return None
