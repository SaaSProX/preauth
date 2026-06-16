"""QA routes: accuracy dashboard, comparison, mismatch reviews, applied mode.

NOTE: This is a placeholder. The QA endpoints (~3000 lines) are still in the
main router.py file pending further refactoring. The endpoints include:
- /qa/accuracy - Agent vs AMAN accuracy dashboard
- /qa/comparison - Comparison listing
- /qa/comparison/summary - Comparison summary stats
- /qa/comparison/{checkin_id} - Single comparison detail
- /applied-mode/config - Applied mode configuration
- /applied-mode/check - Check applied mode eligibility
- /mismatch-reviews - CRUD for mismatch reviews
- /mismatch-reviews/summary - Mismatch review statistics
- /mismatch-reviews/categories - Category definitions

TODO: Extract these endpoints from router.py in a future iteration.
"""

from fastapi import APIRouter

router = APIRouter()

# QA endpoints will be extracted here in a future iteration.
# For now, they remain in the main auth/router.py file.
