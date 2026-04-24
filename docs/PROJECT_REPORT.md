# BlinkAssist — Django + React Edition

End-to-end implementation of **Objectives 1 & 2** of the BlinkAssist review paper, built on:

- **Backend**: Django 4 · Django REST Framework · Django Channels (WebSocket) · OpenCV · MediaPipe · NumPy
- **Frontend**: React 18 · Vite · TypeScript

---

## 1. Run the backend (Django)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

For WebSocket support (Channels) use Daphne instead:
```bash
daphne -b 0.0.0.0 -p 8000 blinkassist.asgi:application
```

Health check: <http://127.0.0.1:8000/api/health/>

## 2. Run the frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>, click **Start camera**, allow webcam access.
Vite proxies `/api/*` to Django on `:8000`.

---

## 3. Project layout

```
blinkassist-dj/
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── blinkassist/        # Django project (settings, urls, asgi, wsgi)
│   └── detector/           # App: EAR pipeline + REST + Channels consumer
│       ├── blink_detector.py   ← MediaPipe + EAR + adaptive τ + state machine
│       ├── views.py            ← /api/detect/, /api/reset/, /api/health/
│       ├── consumers.py        ← ws://…/ws/blink/  (Channels)
│       ├── routing.py
│       └── urls.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts      # proxies /api → Django :8000
│   └── src/
│       ├── App.tsx         ← capture + dashboard UI
│       ├── main.tsx
│       └── styles.css
├── arduino/
│   └── blinkassist.ino     # firmware stub for Objective 5 (future)
└── docs/
    └── PROJECT_REPORT.md   # this file
```

---

## 4. How the code maps to the paper

| Paper section | File / function |
|---|---|
| Objective 1 — non-invasive vision pipeline | `frontend/src/App.tsx` (camera capture) → `backend/detector/views.py::detect_frame` |
| Objective 2 — real-time blink detection    | `backend/detector/blink_detector.py::BlinkDetector.process` |
| Eq. (1) Eye Aspect Ratio (Soukupova & Cech) | `_ear()` |
| Eq. (2) Bilateral averaging EAR_L + EAR_R / 2 | inside `process()` |
| Eq. (3) Adaptive threshold τ = μ − 3σ       | calibration block in `process()` |
| Single / Double / Sustained classification  | state machine in `process()` |

### Constants used

| Symbol | Value | Meaning |
|---|---|---|
| `SINGLE_MIN_MS` | 80   | Minimum duration to count as a blink (rejects micro-noise) |
| `SINGLE_MAX_MS` | 400  | Maximum duration of a normal voluntary blink |
| `DOUBLE_GAP_MS` | 450  | Inter-blink gap to merge two singles into a double |
| `SUSTAINED_MS`  | 2000 | Closed-eye duration that triggers SOS |
| `CALIB_SECONDS` | 10   | Adaptive calibration window |

---

## 5. Tech-stack walkthrough (for the viva)

**Why Django + React?**
- Django gives a clean REST + WebSocket backend with very little boilerplate (DRF + Channels).
- React + TypeScript on Vite makes the dashboard fast, typed, and trivially deployable.
- The split lets the **vision pipeline run server-side** (real Python OpenCV + MediaPipe), exactly as the paper describes — the browser is only a thin client.

**Request flow**
1. React calls `getUserMedia` → renders the webcam in a `<video>`.
2. Every ~83 ms, a hidden `<canvas>` snapshots the frame to a base64 JPEG.
3. The frame is POSTed to `POST /api/detect/`.
4. Django decodes the JPEG (OpenCV), runs MediaPipe FaceMesh, computes EAR, updates the state machine, and returns `{ ear, threshold, calibration, event, counts }`.
5. React renders the metrics, calibration bar and event log live.

**Adaptive thresholding (Eq. 3)**
For the first 10 seconds, the backend collects per-frame EAR samples while the user is asked to look normally. When the window closes, it computes
`τ = max(0.15, μ − 3σ)`. This personalises the detector to each user's eye geometry and lighting, which is exactly what the paper argues for over a hard-coded 0.21 threshold.

**Blink classification**
A small finite state machine tracks whether EAR is currently below τ:
- Closure 80–400 ms → **single** blink.
- Two singles within 450 ms → upgraded to **double**.
- Closure ≥ 2 s → **sustained / SOS**.

**Future objectives**
- Objective 3 (mapping blinks → words/menus): add a Django app `aac/` with a model of menu nodes and a React menu component driven by the event stream.
- Objective 4 (caregiver alerts): use Channels group-send on `sustained` events.
- Objective 5 (hardware buzzer): the `arduino/blinkassist.ino` stub already listens on serial; swap in `pyserial` from the Django side.

---

## 6. Things you can confidently say

- "We use **MediaPipe FaceMesh (468 landmarks)** instead of dlib — it's faster, runs on CPU, and is the current state of the art for browser/edge devices."
- "EAR is computed with **NumPy vector norms** following Soukupova & Cech (2016), and we **average the two eyes** (Eq. 2) for noise robustness."
- "The threshold is **adaptive (μ − 3σ)** over a 10-second calibration window — this handles user-to-user variation in eye geometry."
- "The frontend never does ML — it just captures frames and calls Django. All vision runs in Python on the server, which matches the architecture in the paper."
- "We expose **both REST (`/api/detect/`) and WebSocket (`/ws/blink/`)**. REST is simpler for the demo; Channels gives lower latency for production."
