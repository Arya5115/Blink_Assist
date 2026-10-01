from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Caregiver, EmergencyLog, Event, NotificationLog, Patient, WellnessCheckLog
from .permissions import role_for


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
