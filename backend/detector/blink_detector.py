"""
BlinkAssist core detection (Objectives 1 & 2 of the review paper).

Pipeline:
  Frame  ->  MediaPipe FaceMesh (468 landmarks)
        ->  Eye Aspect Ratio (Soukupova & Cech, 2016) - Eq. (1)
        ->  Bilateral averaging EAR_avg = (EAR_L + EAR_R) / 2 - Eq. (2)
        ->  Adaptive threshold tau = mu - 3*sigma over 10s calibration - Eq. (3)
        ->  State machine: SINGLE / DOUBLE / SUSTAINED (SOS)
"""
import time
import numpy as np
import cv2
import mediapipe as mp

# 6-point eye contours used in the EAR formula
LEFT_EYE  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

SINGLE_MIN_MS  = 80
SINGLE_MAX_MS  = 400
DOUBLE_GAP_MS  = 800
CALIB_SECONDS  = 10


def _ear(landmarks, idx, w, h):
    pts = np.array([[landmarks[i].x * w, landmarks[i].y * h] for i in idx])
    v1 = np.linalg.norm(pts[1] - pts[5])
    v2 = np.linalg.norm(pts[2] - pts[4])
    h_ = np.linalg.norm(pts[0] - pts[3])
    return (v1 + v2) / (2.0 * h_ + 1e-6)


def _points(landmarks, idx, w, h):
    return [
        {"x": round(float(landmarks[i].x * w), 1), "y": round(float(landmarks[i].y * h), 1)}
        for i in idx
    ]


class BlinkDetector:
    """Stateful per-session blink detector."""

    def __init__(self):
        self.mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1, refine_landmarks=True,
            min_detection_confidence=0.5, min_tracking_confidence=0.5,
        )
        self.reset()

    def reset(self):
        self.calib_samples = []
        self.calib_start   = time.time()
        self.threshold     = None
        self.below         = False
        self.below_start   = 0.0
        self.last_blink_end = 0.0
        self.unprocessed_blinks = 0
        self.counts = {"single": 0, "double": 0, "sustained": 0, "total": 0}

    def process(self, bgr):
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        res = self.mesh.process(rgb)
        out = {
            "face": False, "ear": None, "ear_left": None, "ear_right": None, "threshold": self.threshold,
            "calibration": min(1.0, (time.time() - self.calib_start) / CALIB_SECONDS),
            "event": None, "counts": dict(self.counts),
            "blinking": False,
            "eye_landmarks": {"left": [], "right": []},
        }
        if not res.multi_face_landmarks:
            return out

        lm = res.multi_face_landmarks[0].landmark
        ear_l = _ear(lm, LEFT_EYE,  w, h)
        ear_r = _ear(lm, RIGHT_EYE, w, h)
        ear   = (ear_l + ear_r) / 2.0          # Eq. (2)
        out["face"] = True
        out["ear"]  = round(float(ear), 4)
        out["ear_left"] = round(float(ear_l), 4)
        out["ear_right"] = round(float(ear_r), 4)
        out["eye_landmarks"] = {
            "left": _points(lm, LEFT_EYE, w, h),
            "right": _points(lm, RIGHT_EYE, w, h),
        }

        now = time.time()

        # ---- Adaptive calibration: tau = mu - 3*sigma  (Eq. 3) ----
        if self.threshold is None:
            self.calib_samples.append(ear)
            if out["calibration"] >= 1.0 and len(self.calib_samples) > 30:
                arr = np.array(self.calib_samples)
                self.threshold = float(max(0.15, arr.mean() - 3 * arr.std()))
                out["threshold"] = round(self.threshold, 4)
            return out

        # Check for expired gap to emit events smoothly
        if self.unprocessed_blinks > 0 and not self.below and (now - self.last_blink_end) * 1000.0 > DOUBLE_GAP_MS:
            blinks = self.unprocessed_blinks
            self.unprocessed_blinks = 0
            
            if blinks >= 5:
                self.counts["sustained"] += 1
                self.counts["total"] += 1
                out["event"] = {"type": "sustained", "duration_ms": int((now - self.last_blink_end) * 1000.0)}
            elif blinks >= 2:
                self.counts["double"] += 1
                self.counts["total"] += 1
                out["event"] = {"type": "double", "duration_ms": int((now - self.last_blink_end) * 1000.0)}
            elif blinks == 1:
                self.counts["single"] += 1
                self.counts["total"] += 1
                out["event"] = {"type": "single", "duration_ms": int((now - self.last_blink_end) * 1000.0)}

        # ---- State machine ----
        if ear < self.threshold and not self.below:
            self.below = True
            self.below_start = now
        elif ear >= self.threshold and self.below:
            self.below = False
            dur_ms = (now - self.below_start) * 1000.0
            if SINGLE_MIN_MS <= dur_ms <= SINGLE_MAX_MS:
                self.unprocessed_blinks += 1
                self.last_blink_end = now

        out["counts"] = dict(self.counts)
        out["threshold"] = round(self.threshold, 4)
        out["blinking"] = self.below
        return out
