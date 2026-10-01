"""Notification delivery and audit boundary."""
import hashlib
import json
import os
import secrets
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import Caregiver, Event, NotificationLog, TelegramLinkToken


def broadcast(patient_id, event_name, payload):
    layer = get_channel_layer()
    async_to_sync(layer.group_send)(f"patient_{patient_id}", {"type": "platform.event", "event": event_name, "payload": payload})


def create_event(patient, event_type, action, metadata=None, status=Event.Status.SUCCESS):
    event = Event.objects.create(patient=patient, event_type=event_type, action=action, metadata=metadata or {}, status=status)
    broadcast(patient.id, "event.created", {"id": event.id, "type": event_type, "action": action, "status": status})
    return event


def notify_caregivers(event):
    """Send explicit caregiver alerts to linked Telegram chats and audit delivery."""
    for caregiver in event.patient.caregivers.all():
        _send_telegram(event, caregiver)
        NotificationLog.objects.create(
            event=event, caregiver=caregiver, channel="PUSH", status="DELIVERED",
            attempts=1, delivered_at=timezone.now(), provider_id="websocket",
        )
    telegram_logs = NotificationLog.objects.filter(event=event, channel=NotificationLog.Channel.TELEGRAM)
    if telegram_logs.filter(status="SENT").exists():
        action, status = "Caregiver Telegram alert sent", Event.Status.SUCCESS
    elif telegram_logs.filter(status="FAILED").exists():
        action, status = "Caregiver Telegram delivery failed", Event.Status.FAILED
    elif telegram_logs.exists():
        action, status = "Caregiver Telegram not configured", Event.Status.FAILED
    else:
        action, status = "No linked caregiver for Telegram alert", Event.Status.FAILED
    audit = Event.objects.create(patient=event.patient, event_type=Event.Type.NOTIFICATION, action=action, status=status, metadata={"source_event_id": event.id})
    broadcast(event.patient_id, "notification.audit", {"id": audit.id, "action": action, "status": status})
    broadcast(event.patient_id, "notification.delivered", {"event_id": event.id, "channel": "PUSH"})


def create_telegram_link(caregiver):
    """Create a short-lived, single-use secret and return its Telegram deep link."""
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if not bot_username or not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        raise RuntimeError("Telegram bot token and username must be configured.")
    TelegramLinkToken.objects.filter(caregiver=caregiver, used_at__isnull=True).delete()
    token = secrets.token_urlsafe(24)
    TelegramLinkToken.objects.create(
        caregiver=caregiver,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    return f"https://t.me/{bot_username}?start={token}"


def link_telegram_chat(token, chat_id):
    """Consume a valid Telegram deep-link token and bind its private chat once."""
    if not token or not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return False
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    try:
        with transaction.atomic():
            link = TelegramLinkToken.objects.select_for_update().select_related("caregiver").get(token_hash=token_hash)
            if link.used_at or link.expires_at <= timezone.now():
                return False
            if Caregiver.objects.exclude(pk=link.caregiver_id).filter(telegram_chat_id=chat_id).exists():
                return False
            link.caregiver.telegram_chat_id = chat_id
            link.caregiver.save(update_fields=["telegram_chat_id"])
            link.used_at = timezone.now()
            link.save(update_fields=["used_at"])
            return True
    except (TelegramLinkToken.DoesNotExist, IntegrityError):
        return False


def _send_telegram(event, caregiver):
    log = NotificationLog.objects.create(
        event=event, caregiver=caregiver, channel=NotificationLog.Channel.TELEGRAM, attempts=1,
    )
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not caregiver.telegram_chat_id:
        log.status = "NO_RECIPIENT"
    elif not bot_token:
        log.status = "NOT_CONFIGURED"
    else:
        payload = json.dumps({
            "chat_id": caregiver.telegram_chat_id,
            "text": f"BlinkAssist alert for {event.patient}: {event.action}",
        }).encode()
        request = Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode())
            if result.get("ok"):
                message = result.get("result", {})
                log.status = "SENT"
                log.provider_id = str(message.get("message_id", ""))[:128]
                log.delivered_at = timezone.now()
            else:
                log.status = "FAILED"
                log.provider_id = str(result.get("description", "Telegram rejected the message."))[:128]
        except HTTPError as exc:
            log.status = "FAILED"
            log.provider_id = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:100]}"[:128]
        except (URLError, TimeoutError, ValueError) as exc:
            log.status = "FAILED"
            log.provider_id = f"{type(exc).__name__}: Telegram request failed"[:128]
    log.save(update_fields=["status", "provider_id", "delivered_at"])


def handle_telegram_update(update):
    """Process a bot /start deep-link update; return a new long-poll offset."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = str(message.get("text", ""))
    parts = text.split(maxsplit=1)
    if chat.get("type") == "private" and len(parts) == 2 and parts[0].split("@", 1)[0] == "/start":
        linked = link_telegram_chat(parts[1].strip(), chat.get("id"))
        answer = "Telegram is connected to your BlinkAssist caregiver account." if linked else "That connection link is invalid or expired. Please create a new link in BlinkAssist."
        send_telegram_message(chat["id"], answer)
    return int(update.get("update_id", 0)) + 1


def send_telegram_message(chat_id, text):
    """Send a Telegram message and raise on provider/API failures."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("Telegram bot token is not configured.")
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram rejected the message."))


