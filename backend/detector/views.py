"""
REST endpoints. The frontend POSTs base64 JPEG frames; we run MediaPipe + EAR
and return the detection state. One detector per process (demo).
"""
import base64
import threading
import numpy as np
import cv2
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .blink_detector import BlinkDetector

_detector = None
_detector_lock = threading.RLock()


@api_view(["GET"])
@permission_classes([AllowAny])
def health(_):
    return Response({"status": "ok", "service": "BlinkAssist Django backend"})


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_state(_):
    global _detector
    with _detector_lock:
        if _detector is not None:
            _detector.reset()
    return Response({"reset": True})


@api_view(["POST"])
@permission_classes([AllowAny])
def detect_frame(request):
    data = request.data.get("image")
    if not data:
        return Response({"error": "missing image"}, status=400)
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        buf = base64.b64decode(data)
        arr = np.frombuffer(buf, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return Response({"error": "decode failed"}, status=400)
    except Exception as e:
        return Response({"error": str(e)}, status=400)

    global _detector
    try:
        # FaceMesh is stateful and not thread-safe. Django's development server
        # can receive the next camera frame before the prior request completes.
        with _detector_lock:
            if _detector is None:
                _detector = BlinkDetector()
            result = _detector.process(bgr)
        return Response(result)
    except Exception:
        # Keep stack traces in the server logs; never expose implementation
        # details to an unauthenticated camera client.
        import logging
        logging.getLogger(__name__).exception("Blink detector processing failed")
        return Response({"error": "Detector temporarily unavailable. Please retry."}, status=503)
