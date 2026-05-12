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
async def test_D3_db_layer_does_not_enforce_trial_paid_exclusivity(fresh_db):
    """D3. After H2 fix, cmd_trial refuses these users at the handler layer; the
    DB-level facts this test asserts are still valid (DB doesn't enforce the
    rule). Documents the design: exclusivity is a handler-level invariant, not a
    schema constraint — see D4 for the handler-level guard.
    """
    user_id = 789
    # User has a paid vpn_base sub
    await create_subscription(
        user_id=user_id, plan="vpn_base", payment_id="paid_789",
        stars_paid=145,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    # The DB-level guard correctly says "active sub exists"
    assert await has_active_subscription(user_id) is True

    # Nothing at the DB layer prevents inserting a second subscription with
    # plan='vpn_trial' for the same user. The fix lives in cmd_trial (handler
    # level), not at the DB constraint — D4 covers that path.
    sub_id = await create_subscription(
        user_id=user_id, plan="vpn_trial",
        payment_id="trial_789_overlap",
        stars_paid=0, expires_at=datetime.utcnow() + timedelta(days=3),
    )
    assert sub_id > 0


@pytest.mark.asyncio
async def test_D4_cmd_trial_rejects_user_with_active_paid_sub(fresh_db):
    """D4. Defense-in-depth at the handler layer: cmd_trial must reject users
    with any active subscription before issuing a free trial.

    Without this guard a user with a paid vpn_max could /trial and walk away
    with an extra free peer (extra slot on the server, extra config row).
    The schema can't express "at most one active sub per user", so this
    invariant is enforced in handlers/admin.py:cmd_trial.

    We exercise the handler via a mocked aiogram Message and prove the early
    return path is taken (the warning text is shown, no provisioning helpers
    are invoked).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    user_id = 555
    # User already has an active paid sub
    await create_subscription(
        user_id=user_id, plan="vpn_max", payment_id="paid_555",
        stars_paid=360,
        expires_at=datetime.utcnow() + timedelta(days=30),
    )
    assert await has_active_subscription(user_id) is True

    # Build a fake aiogram Message
    message = MagicMock()
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.answer = AsyncMock(return_value=None)

    # Patch provisioning + server helpers so the test fails loudly if the
    # guard is ever removed and execution falls through.
    with patch("services.vpnctl_client.provision_peer", new=AsyncMock()) as prov_mock, \
         patch("services.database.get_best_server", new=AsyncMock(return_value=None)) as server_mock:
        from handlers.admin import cmd_trial
        await cmd_trial(message)

    # The handler must have answered with the refusal text...
    message.answer.assert_awaited_once()
    refusal_text = message.answer.await_args.args[0]
    assert refusal_text.startswith("У тебя уже активная подписка"), refusal_text

    # ...and must NOT have proceeded to provision a peer or pick a server.
    prov_mock.assert_not_awaited()
    server_mock.assert_not_awaited()
