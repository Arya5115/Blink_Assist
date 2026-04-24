# BlinkAssist - Django + React

Vision-based assistive communication for ALS / locked-in patients.
Implements **Objectives 1 & 2** of the review paper.

```
backend/   Django 4 + DRF + Channels + OpenCV + MediaPipe
frontend/  React 18 + Vite + TypeScript
```

See `docs/PROJECT_REPORT.md` for full setup, architecture, and viva notes.

## Quick start

```powershell
# Terminal 1 - backend
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8000

# Terminal 2 - frontend
cd frontend
npm install
npm run dev
# open http://localhost:5173
```
# Blink_Assist
