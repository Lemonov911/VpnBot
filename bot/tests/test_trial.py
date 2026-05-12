"""
D. Trial-flow DB guards.

NOTE: handlers/admin.py does NOT (yet) contain a cmd_trial / /trial handler at
the time of writing — only /admin, /gift, /send. So we cannot exercise the
aiogram handler directly. Instead we freeze the DB-level facts that the
upcoming fix will need to consult:

  - has_active_subscription(uid) correctly reports paid subs
  - a "vpn_trial" subscription is just another row in `subscriptions`
  - the check used to gate trial issuance is `has_active_subscription`

After cmd_trial lands and the fix is applied, the test labelled
test_D3_currently_can_stack_trial_on_paid_baseline will need to be replaced
with a handler-level test that asserts the trial is refused when a paid sub
is active.
"""
from datetime import datetime, timedelta

import pytest

from services.database import (
    create_subscription,
    has_active_subscription,
    get_active_subscription,
)


@pytest.mark.asyncio
async def test_D1_user_with_zero_subs_can_be_granted_trial(fresh_db):
    """D1. Brand new user (no subs) → has_active_subscription is False, so the
    trial guard would allow issuance."""
    assert await has_active_subscription(123) is False
    # Simulate granting a trial — should succeed (no DB constraint blocks it)
    sub_id = await create_subscription(
        user_id=123, plan="vpn_trial", payment_id="trial_123_xyz",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=3),
    )
    assert sub_id > 0
    sub = await get_active_subscription(123)
    assert sub is not None
    assert sub["plan"] == "vpn_trial"


@pytest.mark.asyncio
async def test_D2_user_with_prior_trial_blocked_by_active_check(fresh_db):
    """D2. User who already has an active vpn_trial sub → has_active_subscription
    returns True, blocking another grant via the standard guard.

    (The upcoming fix will additionally check for ANY plan, not just trials —
    but the existing-trial path already works.)
    """
    user_id = 456
    await create_subscription(
        user_id=user_id, plan="vpn_trial", payment_id="trial_456_abc",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=3),
    )
    assert await has_active_subscription(user_id) is True


@pytest.mark.asyncio
async def test_D3_currently_can_stack_trial_on_paid_baseline(fresh_db):
    """D3. BASELINE BUG: a user with an active PAID subscription (vpn_base)
    has has_active_subscription=True — meaning a properly-implemented trial
    handler MUST refuse them. The upcoming fix to cmd_trial will add this
    guard. This test records that the DB-level guard data is in place.

    TODO: after the fix, add a handler-level test that asserts the trial is
    refused for this user (status flag returned / no new sub row inserted).
    """
    user_id = 789
    # User has a paid vpn_base sub
    await create_subscription(
        user_id=user_id, plan="vpn_base", payment_id="paid_789",
        stars_paid=145,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    # The DB-level guard would correctly say "active sub exists"
    assert await has_active_subscription(user_id) is True

    # CURRENT BASELINE: nothing at the DB layer prevents inserting a second
    # subscription with plan='vpn_trial' for the same user. The fix lives in
    # cmd_trial (handler level), not at the DB constraint.
    sub_id = await create_subscription(
        user_id=user_id, plan="vpn_trial",
        payment_id="trial_789_overlap",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=3),
    )
    assert sub_id > 0
