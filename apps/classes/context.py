"""
Tells every page whether the person signed in gives massages.

This lives in the classes app rather than beside the other role flags in
`gyms`, because whether somebody gives massages is written on their trainer
page, and `gyms` sits below `classes` and may not read it.
"""

from apps.classes.models import TrainerProfile
from apps.gyms.models import Role


def trainer_work(request):
    """
    `gives_massages` is for the menu: a trainer who only takes classes should
    never be shown a massage diary, a massage tab, or anybody's notes.
    """
    assignment = getattr(request, "assignment", None)
    if assignment is None or assignment.role != Role.TRAINER:
        return {}
    profile = (
        TrainerProfile.objects.for_gym(assignment.gym)
        .filter(staff=assignment)
        .only("gives_massages")
        .first()
    )
    return {"gives_massages": profile is not None and profile.gives_massages}
