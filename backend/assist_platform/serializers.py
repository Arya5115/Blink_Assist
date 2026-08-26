from rest_framework import serializers
from .models import CalibrationProfile, Caregiver, CommunicationLog, DatasetFrame, DatasetSession, Event, Patient, PatientStatusLog, WellnessCheckLog


class PatientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.get_full_name", read_only=True)
    class Meta:
        model = Patient
        fields = ("id", "name", "age", "room_number", "calibration_threshold")


class CaregiverSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="user.get_full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    class Meta:
        model = Caregiver
        fields = ("id", "name", "email", "phone_number", "whatsapp_number", "patients")


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = "__all__"
        read_only_fields = ("patient", "created_at")


class CommunicationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommunicationLog
        fields = "__all__"
        read_only_fields = ("patient", "created_at")


class CalibrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalibrationProfile
        fields = "__all__"
        read_only_fields = ("patient", "created_at")


class StatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientStatusLog
        fields = "__all__"


class WellnessSerializer(serializers.ModelSerializer):
    class Meta:
        model = WellnessCheckLog
        fields = "__all__"


class DatasetSessionSerializer(serializers.ModelSerializer):
    frame_count = serializers.IntegerField(source="frames.count", read_only=True)
    class Meta:
        model = DatasetSession
        fields = "__all__"
        read_only_fields = ("patient",)


class DatasetFrameSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetFrame
        fields = "__all__"
