from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import EmergencyLog, Event, Patient, WellnessCheckLog


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
