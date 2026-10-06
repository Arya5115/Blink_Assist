"""
Optional WebSocket consumer (Django Channels) for low-latency streaming.
Frontend may use either /api/detect/ (REST) or ws://host/ws/blink/ (Channels).
"""
import base64, json
import numpy as np, cv2
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from .blink_detector import BlinkDetector


class BlinkConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.detector = BlinkDetector()
        await self.accept()

    async def receive_json(self, content, **kwargs):
        if content.get("cmd") == "reset":
            self.detector.reset()
            await self.send_json({"reset": True})
            return
        img = content.get("image", "")
        if "," in img:
            img = img.split(",", 1)[1]
        try:
            arr = np.frombuffer(base64.b64decode(img), np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            await self.send_json({"error": str(e)}); return
        if bgr is None:
            await self.send_json({"error": "decode failed"}); return
        await self.send_json(self.detector.process(bgr))
