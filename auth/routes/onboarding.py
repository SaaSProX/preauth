"""Onboarding routes: organization management, team, user profile.

NOTE: This is a placeholder. These endpoints are still in the main router.py
file pending further refactoring. The endpoints include:
- /onboarding/orgs - List organizations (platform admin)
- /onboarding/create-org - Create new organization
- /onboarding/orgs/{org_id} - Update organization
- /team - List team members
- /team-member/{email} - Remove team member
- /invite-member - Send team invitation
- /me - Current user profile

TODO: Extract these endpoints from router.py in a future iteration.
"""

from fastapi import APIRouter

router = APIRouter()

# Onboarding/team endpoints will be extracted here in a future iteration.
# For now, they remain in the main auth/router.py file.
