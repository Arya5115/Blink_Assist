"""Notification delivery and audit boundary."""
import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from .models import Event, NotificationLog


def broadcast(patient_id, event_name, payload):
    layer = get_channel_layer()
    async_to_sync(layer.group_send)(f"patient_{patient_id}", {"type": "platform.event", "event": event_name, "payload": payload})


def create_event(patient, event_type, action, metadata=None, status=Event.Status.SUCCESS):
    event = Event.objects.create(patient=patient, event_type=event_type, action=action, metadata=metadata or {}, status=status)
    broadcast(patient.id, "event.created", {"id": event.id, "type": event_type, "action": action, "status": status})
    return event


def notify_caregivers(event):
    """Deliver SMS through Twilio when configured; always retain an audit trail."""
    for caregiver in event.patient.caregivers.all():
        _send_twilio(event, caregiver, "SMS", caregiver.phone_number)
        # WhatsApp is fully optional; do not create a misleading failure row unless
        # the sender has deliberately configured it.
        if os.environ.get("TWILIO_WHATSAPP_FROM"):
            _send_twilio(event, caregiver, "WHATSAPP", caregiver.whatsapp_number, whatsapp=True)
        NotificationLog.objects.create(
            event=event, caregiver=caregiver, channel="PUSH", status="DELIVERED",
            attempts=1, delivered_at=timezone.now(), provider_id="websocket",
        )
    external = NotificationLog.objects.filter(event=event).exclude(channel="PUSH")
    if external.filter(status="SENT").exists():
        action, status = "Caregiver SMS sent", Event.Status.SUCCESS
    elif external.filter(status="FAILED").exists():
        action, status = "Caregiver SMS delivery failed", Event.Status.FAILED
    elif external.exists():
        action, status = "Caregiver SMS not configured", Event.Status.FAILED
    else:
        action, status = "No caregiver assigned for notification", Event.Status.FAILED
    audit = Event.objects.create(patient=event.patient, event_type=Event.Type.NOTIFICATION, action=action, status=status, metadata={"source_event_id": event.id})
    broadcast(event.patient_id, "notification.audit", {"id": audit.id, "action": action, "status": status})
    broadcast(event.patient_id, "notification.delivered", {"event_id": event.id, "channel": "PUSH"})


def _send_twilio(event, caregiver, channel, recipient, whatsapp=False):
    """Use Twilio's REST API without pretending a queue item was delivered.

    Required environment: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and
    TWILIO_FROM_NUMBER. Some Twilio trial/regulatory routes require an approved
    Content Template; set TWILIO_CONTENT_SID when the console provides one.
    """
    log = NotificationLog.objects.create(event=event, caregiver=caregiver, channel=channel, attempts=1)
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_WHATSAPP_FROM" if whatsapp else "TWILIO_FROM_NUMBER", "")
    if not recipient:
        log.status = "NO_RECIPIENT"
    elif not all((account_sid, auth_token, from_number)):
        log.status = "NOT_CONFIGURED"
    else:
        prefix = "whatsapp:" if whatsapp else ""
        try:
            body = f"BlinkAssist alert for {event.patient}: {event.action}"
            fields = {"To": f"{prefix}{recipient}", "From": f"{prefix}{from_number}"}
            content_sid = os.environ.get("TWILIO_CONTENT_SID", "")
            if content_sid:
                fields.update({"ContentSid": content_sid, "ContentVariables": json.dumps({"1": str(event.patient), "2": event.action})})
            else:
                fields["Body"] = body
            payload = urlencode(fields).encode()
            credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
            request = Request(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
                data=payload, headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"}, method="POST",
            )
            with urlopen(request, timeout=10) as response:
                provider_id = json.loads(response.read().decode()).get("sid", "")
            log.status, log.provider_id, log.delivered_at = "SENT", provider_id, timezone.now()
        except HTTPError as exc:
            # Twilio returns the useful explanation in its response body.
            detail = exc.read().decode(errors="replace")[:110]
            log.status, log.provider_id = "FAILED", f"HTTP {exc.code}: {detail}"[:128]
        except (URLError, TimeoutError, ValueError) as exc:
            log.status, log.provider_id = "FAILED", str(exc)[:128]
    log.save(update_fields=["status", "provider_id", "delivered_at"])
