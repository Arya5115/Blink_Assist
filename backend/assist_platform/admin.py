from django.contrib import admin
from .models import CalibrationProfile, Caregiver, CommunicationLog, DatasetFrame, DatasetSession, EmergencyLog, Event, NotificationLog, Patient, PatientStatusLog, WellnessCheckLog

admin.site.register((Patient, Caregiver, CalibrationProfile, Event, CommunicationLog, PatientStatusLog, WellnessCheckLog, NotificationLog, DatasetSession, DatasetFrame, EmergencyLog))
