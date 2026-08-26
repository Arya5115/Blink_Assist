from django.conf import settings
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class Patient(TimestampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="patient_profile")
    room_number = models.CharField(max_length=32, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    calibration_threshold = models.FloatField(default=0.21)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Caregiver(TimestampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="caregiver_profile")
    phone_number = models.CharField(max_length=32, blank=True)
    whatsapp_number = models.CharField(max_length=32, blank=True)
    patients = models.ManyToManyField(Patient, related_name="caregivers", blank=True)


class CalibrationProfile(TimestampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="calibrations")
    open_ear_mean = models.FloatField()
    open_ear_stddev = models.FloatField(default=0)
    threshold = models.FloatField()
    sample_count = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)


class Event(TimestampedModel):
    class Type(models.TextChoices):
        BLINK = "BLINK", "Blink"
        COMMUNICATION = "COMMUNICATION", "Communication"
        APPLIANCE = "APPLIANCE", "Appliance"
        WELLNESS = "WELLNESS", "Wellness"
        EMERGENCY = "EMERGENCY", "Emergency"
        NOTIFICATION = "NOTIFICATION", "Notification"
        ACTIVITY = "ACTIVITY", "Activity"
        ARDUINO = "ARDUINO", "Arduino"
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=Type.choices)
    action = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCESS)
    metadata = models.JSONField(default=dict, blank=True)


class CommunicationLog(TimestampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="communication_logs")
    message = models.CharField(max_length=255)
    spoken = models.BooleanField(default=False)
    undone = models.BooleanField(default=False)


class PatientStatusLog(TimestampedModel):
    class State(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        IDLE = "IDLE", "Idle"
        SLEEP_CANDIDATE = "SLEEP_CANDIDATE", "Possibly sleeping"
        FACE_LOST = "FACE_LOST", "Face lost"
        WELLNESS_CHECK_PENDING = "WELLNESS_CHECK_PENDING", "Wellness check pending"
        UNRESPONSIVE = "UNRESPONSIVE", "Unresponsive"
        EMERGENCY = "EMERGENCY", "Emergency"
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="status_logs")
    state = models.CharField(max_length=32, choices=State.choices)
    reason = models.CharField(max_length=255, blank=True)


class WellnessCheckLog(TimestampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="wellness_checks")
    prompted_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    successful = models.BooleanField(default=False)


class NotificationLog(TimestampedModel):
    class Channel(models.TextChoices):
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"
        WHATSAPP = "WHATSAPP", "WhatsApp"
        PUSH = "PUSH", "Push"
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="notifications")
    caregiver = models.ForeignKey(Caregiver, on_delete=models.CASCADE, related_name="notifications")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    status = models.CharField(max_length=16, default="PENDING")
    attempts = models.PositiveSmallIntegerField(default=0)
    provider_id = models.CharField(max_length=128, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)


class DatasetSession(TimestampedModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="dataset_sessions")
    name = models.CharField(max_length=120)
    is_recording = models.BooleanField(default=False)


class DatasetFrame(TimestampedModel):
    session = models.ForeignKey(DatasetSession, on_delete=models.CASCADE, related_name="frames")
    frame_id = models.CharField(max_length=80)
    ear = models.FloatField()
    label = models.CharField(max_length=32, default="Noise")
    predicted_label = models.CharField(max_length=32, default="Noise")


class EmergencyLog(TimestampedModel):
    """Lifecycle record for an emergency escalation; the Event remains the audit stream."""
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="emergency_log")
    trigger = models.CharField(max_length=255)
    countdown_seconds = models.PositiveSmallIntegerField(default=5)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
