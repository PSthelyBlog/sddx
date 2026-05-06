"""PaymentAttempt — one billing cycle's payment with bounded retries.

A direct subclass of ``sddx.std.Retry``. Each PaymentAttempt represents one
billing period for one subscription. The runner emits ``retry.attempt_requested``
on each attempt; the FakePaymentProvider responds with payment.captured or
payment.declined; the Retry advances accordingly.

The instance id encodes both subscription and cycle so each cycle is a
separate state machine instance: ``"<subscription_id>:cycle<n>"``.
"""

from sddx.std import Retry


class PaymentAttempt(Retry):
    """Billing-cycle payment with retry-on-decline.

    Context fields (in addition to those required by Retry):
        retry_id (str): "<subscription_id>:cycle<n>"
        subscription_id (str): customer-facing subscription identifier
        cycle (int): which billing period this represents (1, 2, 3, ...)
        amount (float): the charge amount in dollars
        max_attempts (int): from Retry — typically 3
        base_delay (float): from Retry — seconds; 60.0 for a one-minute first backoff
        attempts (int): mutated by Retry as attempts are made
    """

    SUCCESS_EVENT = "payment.captured"
    FAILURE_EVENT = "payment.declined"
