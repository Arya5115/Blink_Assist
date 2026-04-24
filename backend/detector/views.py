"""
REST endpoints. The frontend POSTs base64 JPEG frames; we run MediaPipe + EAR
and return the detection state. One detector per process (demo).
"""
import base64
import numpy as np
import cv2
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .blink_detector import BlinkDetector

_detector = None


@api_view(["GET"])
def health(_):
    return Response({"status": "ok", "service": "BlinkAssist Django backend"})


@api_view(["POST"])
def reset_state(_):
    _detector.reset()
    return Response({"reset": True})


@api_view(["POST"])
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
    if _detector is None:
        try:
            _detector = BlinkDetector()
        except Exception as e:
            import traceback
            return Response({"error": "init failed", "trace": traceback.format_exc()}, status=500)

    try:
        return Response(_detector.process(bgr))
    except Exception as e:
        import traceback
        return Response({"error": "process failed", "trace": traceback.format_exc()}, status=500)
