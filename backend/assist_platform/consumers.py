from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import AccessToken
from urllib.parse import parse_qs
from .permissions import ADMIN, CAREGIVER, PATIENT, role_for


@database_sync_to_async
def permitted_patient(user_id, patient_id):
    try:
        user = User.objects.get(pk=user_id, is_active=True)
        role = role_for(user)
        if role == ADMIN:
            return True
        if role == PATIENT:
            return getattr(user, "patient_profile", None) and user.patient_profile.id == patient_id
        if role == CAREGIVER:
            return hasattr(user, "caregiver_profile") and user.caregiver_profile.patients.filter(pk=patient_id).exists()
    except User.DoesNotExist:
        pass
    return False


class PlatformConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.patient_id = int(self.scope["url_route"]["kwargs"]["patient_id"])
        query = parse_qs(self.scope.get("query_string", b"").decode("utf-8"))
        token = query.get("token", [""])[0]
        try:
            user_id = AccessToken(token)["user_id"]
        except Exception:
            await self.close(code=4401)
            return
        if not await permitted_patient(user_id, self.patient_id):
            await self.close(code=4403)
            return
        self.group_name = f"patient_{self.patient_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content):
        # Detector clients publish telemetry only; commands stay server-authoritative.
        await self.channel_layer.group_send(self.group_name, {"type": "platform.event", "event": "telemetry", "payload": content})

    async def platform_event(self, message):
        await self.send_json({"event": message["event"], "payload": message["payload"]})
