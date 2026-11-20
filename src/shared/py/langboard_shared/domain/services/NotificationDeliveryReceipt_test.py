"""Receipt safety properties independent of HTTP transport success."""

import unittest
from dataclasses import replace

from NotificationDeliveryReceipt import (
    NotificationDeliveryFence,
    NotificationDeliveryReceipt,
    NotificationReceiptConflict,
    accept_notification_receipt,
)


class NotificationDeliveryReceiptTest(unittest.TestCase):
    def setUp(self):
        self.fence = NotificationDeliveryFence("aBc12345678", 1, 3, 4)

    def receipt(self, status, **kwargs):
        return NotificationDeliveryReceipt(self.fence, status, **kwargs)

    def apply(self, current, incoming, **kwargs):
        return accept_notification_receipt(
            current, incoming, self.fence,
            expected_revision=current.revision if current else None, **kwargs,
        )

    def test_admission_is_not_confirmation(self):
        result = self.apply(None, self.receipt("adapter_accepted"))
        self.assertEqual(result.status, "adapter_accepted")
        self.assertIsNone(result.provider_message_id)
        with self.assertRaises(ValueError):
            self.receipt("confirmed")
        with self.assertRaises(ValueError):
            self.receipt("adapter_accepted", provider_message_id="provider-message")

    def test_real_message_confirmation_is_retained(self):
        accepted = self.apply(None, self.receipt("adapter_accepted"))
        confirmed = self.apply(accepted, self.receipt("confirmed", provider_message_id="message-1"))
        self.assertEqual((confirmed.status, confirmed.revision), ("confirmed", 2))
        self.assertEqual(accepted.status, "adapter_accepted")
        self.assertIs(self.apply(confirmed, self.receipt("confirmed", provider_message_id="message-1")), confirmed)

    def test_terminal_receipts_do_not_reopen_or_change_message(self):
        for terminal in (self.receipt("confirmed", provider_message_id="message-1"), self.receipt("rejected")):
            for incoming in (
                self.receipt("adapter_accepted"), self.receipt("unknown"),
                self.receipt("confirmed", provider_message_id="message-2"),
            ):
                with self.subTest(terminal=terminal.status, incoming=incoming.status):
                    with self.assertRaises(NotificationReceiptConflict):
                        self.apply(terminal, incoming, reconciled=True)

    def test_unknown_does_not_trigger_automatic_send_or_confirmation(self):
        unknown = self.apply(self.receipt("adapter_accepted"), self.receipt("unknown", reason_code="provider_outcome_unknown"))
        for incoming in (self.receipt("adapter_accepted"), self.receipt("confirmed", provider_message_id="message-1"), self.receipt("rejected")):
            with self.subTest(incoming=incoming.status):
                with self.assertRaises(NotificationReceiptConflict):
                    self.apply(unknown, incoming)
        with self.assertRaises(NotificationReceiptConflict):
            self.apply(unknown, self.receipt("adapter_accepted"), reconciled=True)
        confirmed = self.apply(unknown, self.receipt("confirmed", provider_message_id="message-1"), reconciled=True)
        self.assertEqual((confirmed.status, confirmed.revision), ("confirmed", 3))

    def test_unknown_can_resolve_to_definite_rejection(self):
        result = self.apply(self.receipt("unknown"), self.receipt("rejected", reason_code="provider_rejected"), reconciled=True)
        self.assertEqual(result.status, "rejected")

    def test_provider_identity_cannot_be_replaced_during_reconciliation(self):
        current = self.receipt("unknown", provider_message_id="message-1")
        for message in (None, "message-2"):
            with self.subTest(message=message), self.assertRaises(NotificationReceiptConflict):
                incoming = self.receipt("rejected", provider_message_id=message)
                self.apply(current, incoming, reconciled=True)

    def test_each_event_fence_is_required_and_immutable(self):
        current = self.receipt("adapter_accepted")
        for field, value in (("event_uid", "zBc12345678"), ("event_revision", 2), ("subscription_generation", 4), ("destination_revision", 5)):
            changed = replace(self.fence, **{field: value})
            incoming = NotificationDeliveryReceipt(changed, "adapter_accepted")
            with self.subTest(field=field), self.assertRaises(NotificationReceiptConflict):
                self.apply(current, incoming)

    def test_compare_and_swap_prevents_stale_overwrite(self):
        current = replace(self.receipt("adapter_accepted"), revision=2)
        for expected in (None, 1, 3):
            with self.subTest(expected=expected), self.assertRaises(NotificationReceiptConflict):
                accept_notification_receipt(current, self.receipt("unknown"), self.fence, expected_revision=expected)
        with self.assertRaises(NotificationReceiptConflict):
            accept_notification_receipt(None, self.receipt("unknown"), self.fence, expected_revision=1)

    def test_replay_is_idempotent_and_retains_original_object(self):
        current = replace(self.receipt("unknown", reason_code="provider_outcome_unknown"), revision=7)
        self.assertIs(self.apply(current, self.receipt("unknown", reason_code="provider_outcome_unknown")), current)

    def test_wire_values_cannot_become_host_receipt_revisions(self):
        with self.assertRaises(ValueError):
            self.apply(None, replace(self.receipt("unknown"), revision=100))
        for value in (True, 0, -1, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.fence, event_revision=value)

    def test_transport_status_and_private_error_text_are_not_receipts(self):
        for status in ("delivered", "200", "sent", None, {}):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.receipt(status)
        for reason in ("Bearer private-token", {}, 500):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                self.receipt("unknown", reason_code=reason)

    def test_message_identity_and_flags_are_bounded(self):
        for message in ("", " ", "x" * 201, "message\nsecret", "message\x7f", 5):
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.receipt("confirmed", provider_message_id=message)
        with self.assertRaises(ValueError):
            self.apply(None, self.receipt("unknown"), reconciled="true")


if __name__ == "__main__":
    unittest.main()
