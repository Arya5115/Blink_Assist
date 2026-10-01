from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
import hashlib
import io
import json
import os
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from django.core.management.base import CommandError
from django.core.management import call_command
from rest_framework.test import APIClient

from .models import Caregiver, EmergencyLog, Event, NotificationLog, Patient, SafetyMonitorState, TelegramLinkToken, WellnessCheckLog
from .permissions import role_for
from .services import create_telegram_link, handle_telegram_update, link_telegram_chat, notify_caregivers
from .management.commands.poll_telegram import Command as TelegramPollCommand


class SafetyAndExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("patient", password="safe-password-123")
        self.patient = Patient.objects.create(user=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_event_export_is_limited_to_the_signed_in_patient(self):
        Event.objects.create(patient=self.patient, event_type=Event.Type.COMMUNICATION, action="Need Water")
        other_user = User.objects.create_user("other", password="safe-password-123")
        other_patient = Patient.objects.create(user=other_user)
        Event.objects.create(patient=other_patient, event_type=Event.Type.EMERGENCY, action="Other patient event")

        response = self.client.get("/api/events/export/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Need Water", body)
        self.assertNotIn("Other patient event", body)

    def test_patient_can_complete_wellness_check_and_cancel_emergency(self):
        check = WellnessCheckLog.objects.create(patient=self.patient)
        wellness = self.client.post("/api/safety/wellness_response/", {"successful": True}, format="json")
        self.assertEqual(wellness.status_code, 200)
        check.refresh_from_db()
        self.assertTrue(check.successful)
        self.assertIsNotNone(check.responded_at)

        event = Event.objects.create(patient=self.patient, event_type=Event.Type.EMERGENCY, action="Test", status=Event.Status.PENDING)
        EmergencyLog.objects.create(event=event, trigger="Test")
        cancelled = self.client.post("/api/safety/cancel/", {}, format="json")
        self.assertEqual(cancelled.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CANCELLED)

    def test_wellness_check_waits_for_a_response_before_notifying_caregivers(self):
        caregiver_user = User.objects.create_user("caregiver", password="safe-password-123")
        caregiver = Caregiver.objects.create(user=caregiver_user, phone_number="+15555550100")
        caregiver.patients.add(self.patient)

        response = self.client.post("/api/safety/wellness/", {}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(WellnessCheckLog.objects.filter(patient=self.patient, responded_at__isnull=True).exists())
        self.assertFalse(NotificationLog.objects.exists())

    def test_successful_wellness_response_records_the_expected_event(self):
        WellnessCheckLog.objects.create(patient=self.patient)

        response = self.client.post("/api/safety/wellness_response/", {"successful": True}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Event.objects.filter(patient=self.patient, event_type=Event.Type.WELLNESS, action="Patient is OK").exists())

    def test_call_caregiver_communication_notifies_assigned_caregivers(self):
        caregiver_user = User.objects.create_user("caregiver", password="safe-password-123")
        caregiver = Caregiver.objects.create(user=caregiver_user, phone_number="+15555550100")
        caregiver.patients.add(self.patient)

        response = self.client.post("/api/communications/", {"message": "Call Caregiver"}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(NotificationLog.objects.filter(event__patient=self.patient).exists())

    def test_role_for_uses_profile_when_group_membership_is_missing(self):
        self.user.groups.clear()

        self.assertEqual(role_for(self.user), "patient")

    def test_wellness_response_accepts_falsey_string_values(self):
        WellnessCheckLog.objects.create(patient=self.patient)

        response = self.client.post("/api/safety/wellness_response/", {"successful": "false"}, format="json")

        self.assertEqual(response.status_code, 200)
        check = WellnessCheckLog.objects.get(patient=self.patient)
        self.assertFalse(check.successful)

    @patch("assist_platform.views.notify_caregivers")
    def test_automatic_sleep_turns_mode_on_after_more_than_40_seconds(self, notify):
        boundary = self.client.post("/api/safety/sleep_mode/", {"action": "sleep", "closed_duration_seconds": 40}, format="json")
        self.assertEqual(boundary.status_code, 400)
        self.assertFalse(SafetyMonitorState.objects.get(patient=self.patient).sleep_mode_on)
        notify.assert_not_called()

        asleep = self.client.post("/api/safety/sleep_mode/", {"action": "sleep", "closed_duration_seconds": 40.1}, format="json")
        duplicate = self.client.post("/api/safety/sleep_mode/", {"action": "sleep", "closed_duration_seconds": 41}, format="json")
        self.assertEqual(asleep.status_code, 200)
        self.assertTrue(asleep.data["sleep_mode_on"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Event.objects.filter(patient=self.patient, action="Patient asleep").count(), 1)
        notify.assert_called_once()

    @patch("assist_platform.views.notify_caregivers")
    def test_wake_turns_sleep_mode_off_and_notifies_caregiver(self, notify):
        self.client.post("/api/safety/sleep_mode/", {"action": "sleep", "closed_duration_seconds": 41}, format="json")

        awake = self.client.post("/api/safety/sleep_mode/", {"action": "wake"}, format="json")

        self.assertEqual(awake.status_code, 200)
        self.assertFalse(awake.data["sleep_mode_on"])
        self.assertTrue(Event.objects.filter(patient=self.patient, action="Patient awake").exists())
        self.assertEqual(notify.call_count, 2)

    @patch("assist_platform.views.notify_caregivers")
    def test_camera_loss_is_deduplicated_and_rearmed_on_recovery(self, notify):
        first = self.client.post("/api/safety/sleep_mode/", {"action": "camera_lost"}, format="json")
        duplicate = self.client.post("/api/safety/sleep_mode/", {"action": "camera_lost"}, format="json")
        self.assertTrue(first.data["camera_loss_alerted"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Event.objects.filter(patient=self.patient, action="Camera/face unavailable").count(), 1)
        notify.assert_called_once()

        recovered = self.client.post("/api/safety/sleep_mode/", {"action": "camera_recovered"}, format="json")
        self.assertFalse(recovered.data["camera_loss_alerted"])
        self.client.post("/api/safety/sleep_mode/", {"action": "camera_lost"}, format="json")
        self.assertEqual(Event.objects.filter(patient=self.patient, action="Camera/face unavailable").count(), 2)
        self.assertEqual(notify.call_count, 2)


class TelegramNotificationTests(TestCase):
    def setUp(self):
        self.patient_user = User.objects.create_user("patient", password="safe-password-123")
        self.patient = Patient.objects.create(user=self.patient_user)
        self.caregiver_user = User.objects.create_user("caregiver", password="safe-password-123")
        self.caregiver = Caregiver.objects.create(user=self.caregiver_user)
        self.caregiver.patients.add(self.patient)
        self.client = APIClient()
        self.client.force_authenticate(self.caregiver_user)

    @patch.dict(os.environ, {"TELEGRAM_BOT_USERNAME": "BlinkAssistTestBot", "TELEGRAM_BOT_TOKEN": "test-token"})
    def test_authenticated_caregiver_can_generate_short_lived_deep_link(self):
        response = self.client.post("/api/caregiver/telegram/", {}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["url"].startswith("https://t.me/BlinkAssistTestBot?start="))
        self.assertEqual(TelegramLinkToken.objects.filter(caregiver=self.caregiver).count(), 1)

    def test_patients_cannot_create_caregiver_telegram_link(self):
        self.client.force_authenticate(self.patient_user)

        response = self.client.post("/api/caregiver/telegram/", {}, format="json")

        self.assertEqual(response.status_code, 403)

    @patch.dict(os.environ, {"TELEGRAM_BOT_USERNAME": "BlinkAssistTestBot", "TELEGRAM_BOT_TOKEN": "test-token"})
    def test_telegram_link_token_is_single_use_and_bound_to_caregiver(self):
        link = create_telegram_link(self.caregiver)
        token = parse_qs(urlparse(link).query)["start"][0]

        self.assertTrue(link_telegram_chat(token, 123456789))
        self.assertFalse(link_telegram_chat(token, 123456789))
        self.caregiver.refresh_from_db()
        self.assertEqual(self.caregiver.telegram_chat_id, 123456789)

    def test_expired_link_token_cannot_be_consumed(self):
        token = "expired-link"
        TelegramLinkToken.objects.create(
            caregiver=self.caregiver,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertFalse(link_telegram_chat(token, 123456789))
        self.caregiver.refresh_from_db()
        self.assertIsNone(self.caregiver.telegram_chat_id)

    @patch("assist_platform.services.send_telegram_message")
    def test_private_start_update_links_account_and_confirms_to_chat(self, send_message):
        with patch.dict(os.environ, {"TELEGRAM_BOT_USERNAME": "BlinkAssistTestBot", "TELEGRAM_BOT_TOKEN": "test-token"}):
            token = parse_qs(urlparse(create_telegram_link(self.caregiver)).query)["start"][0]

        next_offset = handle_telegram_update({
            "update_id": 25,
            "message": {"text": f"/start {token}", "chat": {"id": 987654321, "type": "private"}},
        })

        self.assertEqual(next_offset, 26)
        self.caregiver.refresh_from_db()
        self.assertEqual(self.caregiver.telegram_chat_id, 987654321)
        send_message.assert_called_once_with(987654321, "Telegram is connected to your BlinkAssist caregiver account.")

    @patch("assist_platform.services.urlopen")
    def test_alert_is_sent_to_telegram_only_and_delivery_is_audited(self, urlopen_mock):
        self.caregiver.telegram_chat_id = 1122334455
        self.caregiver.save(update_fields=["telegram_chat_id"])
        response = urlopen_mock.return_value.__enter__.return_value
        response.read.return_value = b'{"ok":true,"result":{"message_id":42}}'
        event = Event.objects.create(patient=self.patient, event_type=Event.Type.EMERGENCY, action="Help requested")

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
            notify_caregivers(event)

        telegram_log = NotificationLog.objects.get(event=event, caregiver=self.caregiver, channel=NotificationLog.Channel.TELEGRAM)
        self.assertEqual(telegram_log.status, "SENT")
        self.assertEqual(telegram_log.provider_id, "42")
        self.assertTrue(NotificationLog.objects.filter(event=event, channel=NotificationLog.Channel.PUSH, status="DELIVERED").exists())
        self.assertFalse(NotificationLog.objects.filter(event=event, channel=NotificationLog.Channel.SMS).exists())
        urlopen_mock.assert_called_once()

    def test_unlinked_caregiver_is_not_marked_as_telegram_delivered(self):
        event = Event.objects.create(patient=self.patient, event_type=Event.Type.NOTIFICATION, action="Patient asleep")

        notify_caregivers(event)

        log = NotificationLog.objects.get(event=event, caregiver=self.caregiver, channel=NotificationLog.Channel.TELEGRAM)
        self.assertEqual(log.status, "NO_RECIPIENT")
        self.assertIsNone(log.delivered_at)

    @patch("assist_platform.services.urlopen", side_effect=URLError("offline"))
    def test_telegram_network_error_is_not_recorded_as_sent(self, _urlopen_mock):
        self.caregiver.telegram_chat_id = 1122334455
        self.caregiver.save(update_fields=["telegram_chat_id"])
        event = Event.objects.create(patient=self.patient, event_type=Event.Type.EMERGENCY, action="Help requested")

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}):
            notify_caregivers(event)

        log = NotificationLog.objects.get(event=event, caregiver=self.caregiver, channel=NotificationLog.Channel.TELEGRAM)
        self.assertEqual(log.status, "FAILED")
        self.assertIsNone(log.delivered_at)
        self.assertNotIn("test-token", log.provider_id)

    def test_poller_http_error_message_does_not_include_request_url_or_token(self):
        token = "secret-test-token"
        error = HTTPError(
            f"https://api.telegram.org/bot{token}/getUpdates",
            409,
            "Conflict",
            {},
            io.BytesIO(json.dumps({"description": "Conflict: terminated by other getUpdates request"}).encode()),
        )

        description = TelegramPollCommand._http_error_description(error)

        self.assertIn("Conflict: terminated by other getUpdates request", description)
        self.assertNotIn(token, description)

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_BOT_USERNAME": "BlinkAssistTestBot"})
    @patch("assist_platform.management.commands.poll_telegram.Command._telegram_request")
    def test_poller_rejects_username_that_does_not_match_bot_token(self, telegram_request):
        telegram_request.return_value = {"ok": True, "result": {"username": "DifferentBot"}}

        with self.assertRaises(CommandError) as raised:
            call_command("poll_telegram", stdout=io.StringIO(), stderr=io.StringIO())

        self.assertIn("belongs to @DifferentBot", str(raised.exception))

