"""Auth module routes.

Split from the monolithic auth/router.py for better maintainability.

Completed modules:
- auth: Register, login
- api_keys: API key management
- gmail: Gmail integration
- support: Support message listing
- preauth: Preauth operations (retry, send-decision, comments)

Pending extraction (still in main router.py):
- preauth-dashboard: Large dashboard endpoint with complex SQL
- qa: QA accuracy, comparison, mismatch reviews (~3000 lines)
- onboarding: Org management, team management
- audit: Audit events, webhook logs
- patients: Patient listing and history
"""

from .auth import router as auth_router
from .api_keys import router as api_keys_router
from .gmail import router as gmail_router
from .support import router as support_router
from .preauth import router as preauth_router

__all__ = [
    "auth_router",
    "api_keys_router", 
    "gmail_router",
    "support_router",
    "preauth_router",
]
