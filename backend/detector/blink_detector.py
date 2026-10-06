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
LONG_BLINK_MS  = 1500
DOUBLE_GAP_MS  = 800
RAPID_WINDOW_MS = 3000
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
        self.mesh = None
        if hasattr(mp, "solutions"):
            self.mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=1, refine_landmarks=True,
                min_detection_confidence=0.5, min_tracking_confidence=0.5,
            )
        else:
            self.face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            self.eye_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml"
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
        self.blink_ends = []
        self.counts = {"single": 0, "double": 0, "triple": 0, "sustained": 0, "total": 0}

    def process(self, bgr):
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        out = {
            "face": False, "ear": None, "ear_left": None, "ear_right": None, "threshold": self.threshold,
            "calibration": min(1.0, (time.time() - self.calib_start) / CALIB_SECONDS),
            "event": None, "counts": dict(self.counts),
            "blinking": False,
            "eye_landmarks": {"left": [], "right": []},
        }
        if self.mesh is None:
            return self._process_opencv_fallback(bgr, out)

        res = self.mesh.process(rgb)
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
            elif blinks == 3:
                self.counts["triple"] += 1
                self.counts["total"] += 1
                out["event"] = {"type": "triple", "duration_ms": int((now - self.last_blink_end) * 1000.0)}
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
                self.blink_ends.append(now)
                self.blink_ends = [ended for ended in self.blink_ends if (now - ended) * 1000.0 <= RAPID_WINDOW_MS]
                if len(self.blink_ends) >= 5:
                    self.unprocessed_blinks = 0
                    self.blink_ends = []
                    self.counts["sustained"] += 1
                    self.counts["total"] += 1
                    out["event"] = {"type": "sustained", "duration_ms": int(RAPID_WINDOW_MS)}
            elif dur_ms >= LONG_BLINK_MS:
                # Long closure is a dedicated navigation gesture, not a blink command.
                self.unprocessed_blinks = 0
                self.blink_ends = []
                out["event"] = {"type": "long", "duration_ms": int(dur_ms)}

        out["counts"] = dict(self.counts)
        out["threshold"] = round(self.threshold, 4)
        out["blinking"] = self.below
        return out

    def _process_opencv_fallback(self, bgr, out):
        """Use OpenCV cascades when MediaPipe's removed Solutions API is unavailable."""
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
        if len(faces) == 0:
            return out

        x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
        out["face"] = True
        face_gray = gray[y:y + height // 2, x:x + width]
        eyes = self.eye_cascade.detectMultiScale(face_gray, scaleFactor=1.1, minNeighbors=5, minSize=(18, 12))
        eyes = sorted(eyes, key=lambda eye: eye[0])[:2]
        eye_points = []
        ears = []
        for eye_x, eye_y, eye_width, eye_height in eyes:
            points = [
                {"x": float(x + eye_x), "y": float(y + eye_y + eye_height // 2)},
                {"x": float(x + eye_x + eye_width // 3), "y": float(y + eye_y)},
                {"x": float(x + eye_x + 2 * eye_width // 3), "y": float(y + eye_y)},
                {"x": float(x + eye_x + eye_width), "y": float(y + eye_y + eye_height // 2)},
                {"x": float(x + eye_x + 2 * eye_width // 3), "y": float(y + eye_y + eye_height)},
                {"x": float(x + eye_x + eye_width // 3), "y": float(y + eye_y + eye_height)},
            ]
            eye_points.append(points)
            ears.append(eye_height / max(eye_width, 1))

        if len(eyes) < 2:
            # Haar eye detection normally fails when the eyelids are closed.
            # Treat that as a closed-eye sample after calibration instead of
            # returning early and making blinks impossible to detect.
            left_x, right_x = width // 4, width // 2
            eye_width, eye_height = max(width // 5, 18), max(height // 14, 12)
            eye_y = height // 3
            for eye_x in (left_x, right_x):
                eye_points.append([
                    {"x": float(x + eye_x), "y": float(y + eye_y + eye_height // 2)},
                    {"x": float(x + eye_x + eye_width // 3), "y": float(y + eye_y + eye_height // 2)},
                    {"x": float(x + eye_x + 2 * eye_width // 3), "y": float(y + eye_y + eye_height // 2)},
                    {"x": float(x + eye_x + eye_width), "y": float(y + eye_y + eye_height // 2)},
                    {"x": float(x + eye_x + 2 * eye_width // 3), "y": float(y + eye_y + eye_height // 2)},
                    {"x": float(x + eye_x + eye_width // 3), "y": float(y + eye_y + eye_height // 2)},
                ])
            ears = [0.05, 0.05]

        ear = sum(ears) / len(ears)
        out["ear"] = round(float(ear), 4)
        out["ear_left"] = round(float(ears[0]), 4)
        out["ear_right"] = round(float(ears[1]), 4)
        out["eye_landmarks"] = {"left": eye_points[0], "right": eye_points[1]}
        now = time.time()
        if self.threshold is None:
            if len(eyes) < 2:
                out["eye_landmarks"] = {"left": eye_points[0], "right": eye_points[1]}
                return out
            self.calib_samples.append(ear)
            if out["calibration"] >= 1.0 and len(self.calib_samples) > 30:
                arr = np.array(self.calib_samples)
                self.threshold = float(max(0.15, arr.mean() - 3 * arr.std()))
                out["threshold"] = round(self.threshold, 4)
            return out

        if self.unprocessed_blinks > 0 and not self.below and (now - self.last_blink_end) * 1000.0 > DOUBLE_GAP_MS:
            blinks = self.unprocessed_blinks
            self.unprocessed_blinks = 0
            event_type = "sustained" if blinks >= 5 else "triple" if blinks == 3 else "double" if blinks >= 2 else "single"
            self.counts[event_type] += 1
            self.counts["total"] += 1
            out["event"] = {"type": event_type, "duration_ms": int((now - self.last_blink_end) * 1000.0)}

        if ear < self.threshold and not self.below:
            self.below = True
            self.below_start = now
        elif ear >= self.threshold and self.below:
            self.below = False
            duration = (now - self.below_start) * 1000.0
            if SINGLE_MIN_MS <= duration <= SINGLE_MAX_MS:
                self.unprocessed_blinks += 1
                self.last_blink_end = now
                self.blink_ends.append(now)
            elif duration >= LONG_BLINK_MS:
                self.unprocessed_blinks = 0
                out["event"] = {"type": "long", "duration_ms": int(duration)}

        out["counts"] = dict(self.counts)
        out["threshold"] = round(self.threshold, 4)
        out["blinking"] = self.below
        return out
