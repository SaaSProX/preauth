"""Auth module router.

REFACTORING IN PROGRESS (SAA-81)

This file is being split into smaller modules:
- auth/schemas.py - Pydantic models (DONE)
- auth/helpers.py - Shared helper functions (DONE)  
- auth/routes/*.py - Grouped endpoint handlers (IN PROGRESS)

Current state: The large endpoints (preauth-dashboard, QA ~3000 lines, etc.)
remain in this file. Smaller endpoints have been extracted but the router
still includes all functionality for backwards compatibility.

See auth/_router_original.py for the pre-refactor backup.
"""

# Re-export everything from the original router for now
# This maintains backwards compatibility while we incrementally refactor
from auth._router_original import *
from auth._router_original import router

# The schemas and helpers modules are ready for use:
# - from auth.schemas import RegisterPayload, LoginPayload, ...
# - from auth.helpers import is_platform_admin, _dashboard_request, ...

# The route modules demonstrate the target structure:
# - auth/routes/auth.py - Login/register
# - auth/routes/api_keys.py - API key management
# - auth/routes/gmail.py - Gmail integration
# - auth/routes/support.py - Support messages
# - auth/routes/preauth.py - Preauth operations

# In the next iteration, this file will be updated to:
# router = APIRouter(prefix="/auth")
# router.include_router(auth_routes)
# router.include_router(api_keys_routes)
# ... etc
