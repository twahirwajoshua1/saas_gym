"""
Where the desk remembers which branch it is at.

A receptionist who is not tied to one branch chooses it once; the choice is
kept in their session. Several modules need to read it (the desk, registering
a member), so the key and the lookup live here, below all of them.
"""

from apps.gyms.models import Branch

DESK_BRANCH = "desk_branch"


def desk_branch(request) -> Branch | None:
    """
    The branch this desk is at: the staff member's own branch if they have one,
    otherwise the branch they chose, otherwise the gym's only branch. None when
    the gym has several and none has been chosen.
    """
    assignment = getattr(request, "assignment", None)
    if assignment is None:
        return None
    if assignment.branch_id is not None:
        return assignment.branch
    branches = Branch.objects.for_gym(request.gym).filter(is_active=True)
    chosen_id = request.session.get(DESK_BRANCH)
    if chosen_id:
        chosen = branches.filter(public_id=chosen_id).first()
        if chosen is not None:
            return chosen
    only = list(branches[:2])
    return only[0] if len(only) == 1 else None
