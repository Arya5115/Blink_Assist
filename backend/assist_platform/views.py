import os

from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from .models import CalibrationProfile, Caregiver, CommunicationLog, DatasetFrame, DatasetSession, EmergencyLog, Event, NotificationLog, Patient, PatientStatusLog, SafetyMonitorState, WellnessCheckLog
from .serializers import CalibrationSerializer, CaregiverSerializer, CommunicationLogSerializer, DatasetFrameSerializer, DatasetSessionSerializer, EventSerializer, PatientSerializer, StatusSerializer, WellnessSerializer
from .services import broadcast, create_event, create_telegram_link, notify_caregivers
from .permissions import ADMIN, CAREGIVER, PATIENT, require_patient_controller, role_for
from .serial_gateway import ArduinoGateway


def _normalized_role(value):
    return str(value or "").strip().lower()


@api_view(["POST"])
@permission_classes([AllowAny])
def register_account(request):
    """Self-service registration is limited to non-privileged care roles."""
    required = ("username", "password", "name", "role")
    missing = [field for field in required if not request.data.get(field)]
    if missing:
        return Response({"detail": f"Missing: {', '.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)
    role = _normalized_role(request.data["role"])
    if role not in (PATIENT, CAREGIVER):
        return Response({"detail": "Administrator accounts must be provisioned by an administrator."}, status=status.HTTP_403_FORBIDDEN)
    if len(request.data["password"]) < 10:
        return Response({"detail": "Password must contain at least 10 characters."}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username=request.data["username"]).exists():
        return Response({"detail": "That username is already in use."}, status=status.HTTP_409_CONFLICT)
    first_name, *rest = request.data["name"].strip().split(maxsplit=1)
    with transaction.atomic():
        user = User.objects.create_user(username=request.data["username"], password=request.data["password"], first_name=first_name, last_name=rest[0] if rest else "", email=request.data.get("email", ""))
        if role == PATIENT:
            Patient.objects.create(user=user, age=request.data.get("age") or None, room_number=request.data.get("room_number", ""))
        else:
            Caregiver.objects.create(user=user, phone_number=request.data.get("phone_number", ""), whatsapp_number=request.data.get("whatsapp_number", ""))
    refresh = RefreshToken.for_user(user)
    return Response({"role": role, "access": str(refresh.access_token), "refresh": str(refresh)}, status=status.HTTP_201_CREATED)


@api_view(["GET", "POST"])
def caregiver_telegram(request):
    """Return own Telegram link status or create a short-lived bot deep link."""
    if role_for(request.user) != CAREGIVER or not hasattr(request.user, "caregiver_profile"):
        raise PermissionDenied("Only caregivers can connect Telegram notifications.")
    caregiver = request.user.caregiver_profile
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    configured = bool(bot_username and os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
    if request.method == "GET":
        return Response({"connected": caregiver.telegram_chat_id is not None, "bot_username": bot_username, "configured": configured})
    try:
        link = create_telegram_link(caregiver)
    except RuntimeError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"url": link}, status=status.HTTP_201_CREATED)


def patient_for(request):
    patient_id = request.data.get("patient_id") or request.query_params.get("patient_id")
    role = role_for(request.user)
    if role == PATIENT and hasattr(request.user, "patient_profile"):
        if patient_id and int(patient_id) != request.user.patient_profile.id:
            raise PermissionDenied("Patients may only access their own profile.")
        return request.user.patient_profile
    if role == CAREGIVER and hasattr(request.user, "caregiver_profile"):
        if not patient_id:
            raise PermissionDenied("Choose an assigned patient.")
        return request.user.caregiver_profile.patients.get(pk=patient_id)
    if role == ADMIN and patient_id:
        return Patient.objects.get(pk=patient_id)
    raise PermissionDenied("A patient profile is required.")


class PatientViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PatientSerializer
    def get_queryset(self):
        user = self.request.user
        role = role_for(user)
        if role == ADMIN:
            return Patient.objects.select_related("user").all()
        if role == PATIENT and hasattr(user, "patient_profile"):
            return Patient.objects.filter(pk=user.patient_profile.pk)
        if role == CAREGIVER and hasattr(user, "caregiver_profile"):
            return user.caregiver_profile.patients.select_related("user")
        return Patient.objects.none()

    @action(detail=False, methods=["get"])
    def me(self, request):
        role = role_for(request.user)
        if role == PATIENT and hasattr(request.user, "patient_profile"):
            return Response({"role": "patient", "patient": self.get_serializer(request.user.patient_profile).data})
        if role == CAREGIVER and hasattr(request.user, "caregiver_profile"):
            return Response({"role": "caregiver", "patients": PatientSerializer(request.user.caregiver_profile.patients.all(), many=True).data})
        return Response({"role": "admin" if role == ADMIN else "unassigned"})

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """Small, role-scoped dashboard payload; event detail stays paginatable at /events/."""
        patient = patient_for(request)
        events = Event.objects.filter(patient=patient)
        latest_status = patient.status_logs.first()
        notification_logs = NotificationLog.objects.filter(event__patient=patient)
        pending_wellness = patient.wellness_checks.filter(responded_at__isnull=True).first()
        return Response({
            "patient": PatientSerializer(patient).data,
            "status": latest_status.state if latest_status else PatientStatusLog.State.ACTIVE,
            "status_reason": latest_status.reason if latest_status else "",
            "counts": {
                "single": events.filter(event_type=Event.Type.BLINK, action="SINGLE").count(),
                "double": events.filter(event_type=Event.Type.BLINK, action="DOUBLE").count(),
                # Lifecycle audit rows (acknowledged/cancelled) are not separate emergencies.
                "emergency": EmergencyLog.objects.filter(event__patient=patient).count(),
                "communications": events.filter(event_type=Event.Type.COMMUNICATION).count(),
            },
            "system": {
                "websocket": "READY",
                "calibration": "READY" if patient.calibrations.filter(is_active=True).exists() else "PENDING",
                "notifications": "SENT" if notification_logs.filter(status="SENT").exists() else ("FAILED" if notification_logs.filter(status="FAILED").exists() else ("IN_APP_DELIVERED" if notification_logs.filter(status="DELIVERED").exists() else ("NOT_CONFIGURED" if notification_logs.exists() else "STANDBY"))),
                "arduino": "CONFIGURED" if __import__("os").environ.get("ARDUINO_PORT") else "NOT_CONFIGURED",
            },
            "recent_events": EventSerializer(events[:8], many=True).data,
            "wellness": {"pending": bool(pending_wellness), "check_id": pending_wellness.id if pending_wellness else None},
        })


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer
    def get_queryset(self):
        try:
            return Event.objects.filter(patient=patient_for(self.request))
        except PermissionDenied:
            return Event.objects.none()

    def perform_create(self, serializer):
        require_patient_controller(self.request)
        patient = patient_for(self.request)
        event = serializer.save(patient=patient)
        broadcast(patient.id, "event.created", {"id": event.id, "type": event.event_type, "action": event.action, "status": event.status})

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Role-scoped CSV export of the auditable event timeline."""
        import csv
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="blinkassist-events.csv"'
        writer = csv.writer(response)
        writer.writerow(["timestamp", "event_type", "action", "status", "metadata"])
        for event in self.get_queryset():
            writer.writerow([event.created_at.isoformat(), event.event_type, event.action, event.status, event.metadata])
        return response


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Caregiver-visible delivery inbox, scoped to the signed-in care team."""
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        role = role_for(request.user)
        patient_id = request.query_params.get("patient_id")
        logs = NotificationLog.objects.select_related("event__patient__user", "caregiver__user")
        if role == CAREGIVER and hasattr(request.user, "caregiver_profile"):
            logs = logs.filter(caregiver=request.user.caregiver_profile)
            if patient_id:
                logs = logs.filter(event__patient_id=patient_id)
        elif role == ADMIN:
            if patient_id:
                logs = logs.filter(event__patient_id=patient_id)
        else:
            raise PermissionDenied("Only caregivers and administrators may view notification delivery.")
        return Response([{
            "id": log.id, "created_at": log.created_at, "patient": str(log.event.patient),
            "event": log.event.action, "channel": log.channel, "status": log.status,
            "provider_id": log.provider_id, "delivered_at": log.delivered_at,
        } for log in logs[:40]])


class CommunicationViewSet(viewsets.ModelViewSet):
    serializer_class = CommunicationLogSerializer
    def get_queryset(self):
        try:
            return CommunicationLog.objects.filter(patient=patient_for(self.request))
        except PermissionDenied:
            return CommunicationLog.objects.none()
    def perform_create(self, serializer):
        require_patient_controller(self.request)
        patient = patient_for(self.request)
        entry = serializer.save(patient=patient, spoken=True)
        create_event(patient, Event.Type.COMMUNICATION, entry.message, {"communication_id": entry.id})
        if entry.message == "Call Caregiver":
            event = create_event(patient, Event.Type.NOTIFICATION, "Caregiver requested")
            notify_caregivers(event)

    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        require_patient_controller(request)
        entry = self.get_object()
        if (timezone.now() - entry.created_at).total_seconds() > 3:
            return Response({"detail": "Undo window expired"}, status=status.HTTP_409_CONFLICT)
        entry.undone = True
        entry.save(update_fields=["undone"])
        return Response(self.get_serializer(entry).data)


class CalibrationViewSet(viewsets.ModelViewSet):
    serializer_class = CalibrationSerializer
    def get_queryset(self):
        try:
            return CalibrationProfile.objects.filter(patient=patient_for(self.request))
        except PermissionDenied:
            return CalibrationProfile.objects.none()
    def perform_create(self, serializer):
        require_patient_controller(self.request)
        patient = patient_for(self.request)
        CalibrationProfile.objects.filter(patient=patient, is_active=True).update(is_active=False)
        profile = serializer.save(patient=patient, is_active=True)
        patient.calibration_threshold = profile.threshold
        patient.save(update_fields=["calibration_threshold"])


class SafetyViewSet(viewsets.ViewSet):
    @action(detail=False, methods=["post"])
    def status(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        record = PatientStatusLog.objects.create(patient=patient, state=request.data["state"], reason=request.data.get("reason", ""))
        create_event(patient, Event.Type.ACTIVITY, record.state, {"reason": record.reason})
        return Response(StatusSerializer(record).data, status=201)

    @action(detail=False, methods=["get", "post"])
    def sleep_mode(self, request):
        """Record automatic sleep/wake and camera-loss transitions for this patient."""
        require_patient_controller(request)
        patient = patient_for(request)
        monitor, _ = SafetyMonitorState.objects.get_or_create(patient=patient)

        def state_payload():
            return {"sleep_mode_on": monitor.sleep_mode_on, "camera_loss_alerted": monitor.camera_loss_alerted}

        if request.method == "GET":
            return Response(state_payload())

        action_name = str(request.data.get("action", "")).strip().lower()
        if action_name not in {"sleep", "wake", "camera_lost", "camera_recovered"}:
            return Response({"detail": "Unsupported safety monitor action."}, status=status.HTTP_400_BAD_REQUEST)

        closed_duration = None
        if action_name == "sleep":
            try:
                closed_duration = float(request.data.get("closed_duration_seconds", 0))
            except (TypeError, ValueError):
                closed_duration = 0
            if not __import__("math").isfinite(closed_duration) or closed_duration <= 40:
                return Response({"detail": "Sleep Mode requires more than 40 continuous seconds of observed eye closure."}, status=status.HTTP_400_BAD_REQUEST)

        notification_event = None
        with transaction.atomic():
            monitor = SafetyMonitorState.objects.select_for_update().get(pk=monitor.pk)
            if action_name == "sleep" and not monitor.sleep_mode_on:
                monitor.sleep_mode_on = True
                monitor.save(update_fields=["sleep_mode_on"])
                notification_event = create_event(
                    patient, Event.Type.NOTIFICATION, "Patient asleep",
                    {"source": "automatic_sleep_mode", "closed_duration_seconds": closed_duration}, Event.Status.PENDING,
                )
            elif action_name == "wake" and monitor.sleep_mode_on:
                monitor.sleep_mode_on = False
                monitor.save(update_fields=["sleep_mode_on"])
                notification_event = create_event(patient, Event.Type.NOTIFICATION, "Patient awake", {"source": "automatic_sleep_mode"}, Event.Status.PENDING)
            elif action_name == "camera_lost" and not monitor.camera_loss_alerted:
                monitor.camera_loss_alerted = True
                monitor.save(update_fields=["camera_loss_alerted"])
                notification_event = create_event(patient, Event.Type.NOTIFICATION, "Camera/face unavailable", {"source": "camera_monitor"}, Event.Status.PENDING)
            elif action_name == "camera_recovered" and monitor.camera_loss_alerted:
                monitor.camera_loss_alerted = False
                monitor.save(update_fields=["camera_loss_alerted"])
                create_event(patient, Event.Type.ACTIVITY, "Camera/face recovered", {"source": "camera_monitor"})

        if notification_event:
            notify_caregivers(notification_event)
        return Response(state_payload())

    @action(detail=False, methods=["post"])
    def wellness(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        check = WellnessCheckLog.objects.filter(patient=patient, responded_at__isnull=True).first()
        if check:
            return Response(WellnessSerializer(check).data)
        check = WellnessCheckLog.objects.create(patient=patient)
        event = create_event(patient, Event.Type.WELLNESS, "Wellness check requested", {"check_id": check.id}, Event.Status.PENDING)
        PatientStatusLog.objects.create(patient=patient, state=PatientStatusLog.State.WELLNESS_CHECK_PENDING, reason="Waiting for patient response")
        broadcast(patient.id, "wellness.requested", {"check_id": check.id})
        return Response(WellnessSerializer(check).data, status=201)

    @action(detail=False, methods=["post"])
    def wellness_response(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        check = WellnessCheckLog.objects.filter(patient=patient, responded_at__isnull=True).first()
        if not check:
            return Response({"detail": "No wellness check is awaiting a response."}, status=status.HTTP_404_NOT_FOUND)
        successful_value = request.data.get("successful", True)
        if isinstance(successful_value, str):
            successful_value = successful_value.strip().lower() not in {"false", "0", "no", "n", "cancel", "off"}
        check.responded_at = timezone.now()
        check.successful = bool(successful_value)
        check.save(update_fields=["responded_at", "successful"])
        Event.objects.filter(patient=patient, event_type=Event.Type.WELLNESS, metadata__check_id=check.id, status=Event.Status.PENDING).update(status=Event.Status.SUCCESS if check.successful else Event.Status.CANCELLED)
        next_state = PatientStatusLog.State.ACTIVE if check.successful else PatientStatusLog.State.UNRESPONSIVE
        PatientStatusLog.objects.create(patient=patient, state=next_state, reason="Patient confirmed wellness" if check.successful else "Patient did not confirm wellness")
        create_event(patient, Event.Type.WELLNESS, "Patient is OK" if check.successful else "Patient needs help", {"check_id": check.id, "successful": check.successful})
        broadcast(patient.id, "wellness.responded", {"check_id": check.id, "successful": check.successful})
        return Response(WellnessSerializer(check).data)

    @action(detail=False, methods=["post"])
    def emergency(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        event = create_event(patient, Event.Type.EMERGENCY, request.data.get("trigger", "Emergency menu"), status=Event.Status.PENDING)
        EmergencyLog.objects.create(event=event, trigger=event.action, countdown_seconds=request.data.get("countdown_seconds", 5))
        notify_caregivers(event)
        return Response(EventSerializer(event).data, status=201)

    @action(detail=False, methods=["post"])
    def acknowledge(self, request):
        if role_for(request.user) not in (CAREGIVER, ADMIN):
            raise PermissionDenied("Only a caregiver or administrator can acknowledge an emergency.")
        patient = patient_for(request)
        emergency = EmergencyLog.objects.filter(event__patient=patient, acknowledged_at__isnull=True, cancelled_at__isnull=True).first()
        if not emergency:
            return Response({"detail": "No active emergency."}, status=status.HTTP_404_NOT_FOUND)
        emergency.acknowledged_at = timezone.now()
        emergency.acknowledged_by = request.user
        emergency.save(update_fields=["acknowledged_at", "acknowledged_by"])
        emergency.event.status = Event.Status.SUCCESS
        emergency.event.save(update_fields=["status"])
        create_event(patient, Event.Type.EMERGENCY, "Emergency acknowledged", {"emergency_id": emergency.id, "acknowledged_by": request.user.username})
        return Response({"id": emergency.id, "status": "ACKNOWLEDGED"})

    @action(detail=False, methods=["post"])
    def cancel(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        emergency = EmergencyLog.objects.filter(event__patient=patient, acknowledged_at__isnull=True, cancelled_at__isnull=True).first()
        if not emergency:
            return Response({"detail": "No active emergency."}, status=status.HTTP_404_NOT_FOUND)
        emergency.cancelled_at = timezone.now()
        emergency.save(update_fields=["cancelled_at"])
        emergency.event.status = Event.Status.CANCELLED
        emergency.event.save(update_fields=["status"])
        create_event(patient, Event.Type.EMERGENCY, "Emergency cancelled", {"emergency_id": emergency.id}, Event.Status.CANCELLED)
        broadcast(patient.id, "emergency.cancelled", {"id": emergency.id})
        return Response({"id": emergency.id, "status": "CANCELLED"})


class ApplianceViewSet(viewsets.ViewSet):
    """Server-authoritative appliance commands with durable ACK/failure audit entries."""
    @action(detail=False, methods=["post"])
    def command(self, request):
        require_patient_controller(request)
        patient = patient_for(request)
        command = request.data.get("command", "")
        if command not in ArduinoGateway.ALLOWED:
            return Response({"detail": "Unsupported appliance command."}, status=status.HTTP_400_BAD_REQUEST)
        event = create_event(patient, Event.Type.APPLIANCE, command, status=Event.Status.PENDING)
        try:
            acknowledgement = ArduinoGateway().send(command)
            event.status = Event.Status.SUCCESS
            event.metadata = {"acknowledgement": acknowledgement}
        except (RuntimeError, ValueError) as exc:
            event.status = Event.Status.FAILED
            event.metadata = {"error": str(exc)}
        event.save(update_fields=["status", "metadata"])
        broadcast(patient.id, "arduino.command", {"id": event.id, "command": command, "status": event.status, **event.metadata})
        return Response(EventSerializer(event).data, status=201 if event.status == Event.Status.SUCCESS else 503)


class DatasetSessionViewSet(viewsets.ModelViewSet):
    serializer_class = DatasetSessionSerializer
    def get_queryset(self):
        try:
            return DatasetSession.objects.filter(patient=patient_for(self.request))
        except PermissionDenied:
            return DatasetSession.objects.none()
    def perform_create(self, serializer):
        require_patient_controller(self.request)
        serializer.save(patient=patient_for(self.request))

    @action(detail=True, methods=["post"])
    def frame(self, request, pk=None):
        require_patient_controller(request)
        frame = DatasetFrame.objects.create(session=self.get_object(), frame_id=request.data["frame_id"], ear=request.data["ear"], label=request.data.get("label", "Noise"), predicted_label=request.data.get("predicted_label", "Noise"))
        return Response(DatasetFrameSerializer(frame).data, status=201)

    @action(detail=False, methods=["get"])
    def metrics(self, request):
        """Binary blink metrics from human-reviewed labels and detector predictions."""
        frames = DatasetFrame.objects.filter(session__patient=patient_for(request))
        reviewed = frames.exclude(label__iexact="UNREVIEWED")
        total = reviewed.count()
        if not total:
            return Response({"reviewed_frames": 0, "precision": None, "recall": None, "f1": None, "accuracy": None})
        positives = {"BLINK", "SINGLE", "DOUBLE", "LONG", "SUSTAINED"}
        tp = fp = fn = tn = 0
        for frame in reviewed.only("label", "predicted_label"):
            actual = frame.label.upper() in positives
            predicted = frame.predicted_label.upper() in positives
            if actual and predicted: tp += 1
            elif predicted: fp += 1
            elif actual: fn += 1
            else: tn += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        return Response({"reviewed_frames": total, "precision": precision, "recall": recall, "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0, "accuracy": (tp + tn) / total, "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}})


class AccountManagementViewSet(viewsets.ViewSet):
    """Admin-only account provisioning with an explicit Patient/Caregiver role."""
    permission_classes = [permissions.IsAdminUser]

    def list(self, request):
        return Response({
            "patients": PatientSerializer(Patient.objects.select_related("user"), many=True).data,
            "caregivers": CaregiverSerializer(Caregiver.objects.select_related("user"), many=True).data,
        })

    @action(detail=False, methods=["post"], url_path="assign")
    def assign(self, request):
        assignments = request.data.get("assignments", [])
        if assignments:
            for assignment in assignments:
                patient_id = assignment.get("patient_id")
                caregiver_ids = assignment.get("caregiver_ids", [])
                if not patient_id or not caregiver_ids:
                    return Response({"detail": "Each assignment needs a patient and at least one caregiver."}, status=400)
                patient = Patient.objects.filter(pk=patient_id).first()
                caregivers = list(Caregiver.objects.filter(pk__in=caregiver_ids))
                if not patient or len(caregivers) != len(set(caregiver_ids)):
                    return Response({"detail": "One or more assignment records are invalid."}, status=400)
                for caregiver in caregivers:
                    caregiver.patients.add(patient)
            return Response({"assignment_count": len(assignments)})

        caregiver_ids = request.data.get("caregiver_ids", [])
        patient_ids = request.data.get("patient_ids", [])
        caregiver_ids = list(caregiver_ids) if isinstance(caregiver_ids, (list, tuple)) else ([caregiver_ids] if caregiver_ids else [])
        patient_ids = list(patient_ids) if isinstance(patient_ids, (list, tuple)) else ([patient_ids] if patient_ids else [])
        if request.data.get("caregiver_id"):
            caregiver_ids.append(request.data["caregiver_id"])
        if request.data.get("patient_id"):
            patient_ids.append(request.data["patient_id"])
        if not caregiver_ids or not patient_ids:
            return Response({"detail": "Select at least one caregiver and one patient."}, status=400)

        caregivers = list(Caregiver.objects.filter(pk__in=caregiver_ids).select_related("user"))
        patients = list(Patient.objects.filter(pk__in=patient_ids).select_related("user"))
        if len(caregivers) != len(set(caregiver_ids)):
            return Response({"detail": "One or more selected caregivers do not exist."}, status=404)
        if len(patients) != len(set(patient_ids)):
            return Response({"detail": "One or more selected patients do not exist."}, status=404)

        for caregiver in caregivers:
            caregiver.patients.add(*patients)
        return Response({
            "caregivers": CaregiverSerializer(caregivers, many=True).data,
            "patient_count": len(patients),
            "caregiver_count": len(caregivers),
        })

    @transaction.atomic
    def create(self, request):
        required = ("username", "password", "role", "name")
        missing = [field for field in required if not request.data.get(field)]
        if missing:
            return Response({"detail": f"Missing: {', '.join(missing)}"}, status=400)
        role = _normalized_role(request.data["role"])
        if role not in ("patient", "caregiver"):
            return Response({"detail": "Role must be patient or caregiver."}, status=400)
        if User.objects.filter(username=request.data["username"]).exists():
            return Response({"detail": "Username already exists."}, status=409)
        first_name, *rest = request.data["name"].strip().split(maxsplit=1)
        user = User.objects.create_user(username=request.data["username"], password=request.data["password"], first_name=first_name, last_name=rest[0] if rest else "", email=request.data.get("email", ""))
        if role == "patient":
            profile = Patient.objects.create(user=user, age=request.data.get("age") or None, room_number=request.data.get("room_number", ""))
            caregiver_ids = request.data.get("caregiver_ids", [])
            caregiver_ids = list(caregiver_ids) if isinstance(caregiver_ids, (list, tuple)) else ([caregiver_ids] if caregiver_ids else [])
            if request.data.get("caregiver_id"):
                caregiver_ids.append(request.data["caregiver_id"])
            if caregiver_ids:
                caregivers = Caregiver.objects.filter(pk__in=caregiver_ids)
                if caregivers.count() != len(set(caregiver_ids)):
                    return Response({"detail": "One or more selected caregivers do not exist."}, status=400)
                for caregiver in caregivers:
                    caregiver.patients.add(profile)
            return Response({"role": role, "patient": PatientSerializer(profile).data}, status=201)
        profile = Caregiver.objects.create(user=user, phone_number=request.data.get("phone_number", ""), whatsapp_number=request.data.get("whatsapp_number", ""))
        assigned = request.data.get("patient_ids", [])
        profile.patients.set(Patient.objects.filter(pk__in=assigned))
        return Response({"role": role, "caregiver": CaregiverSerializer(profile).data}, status=201)
