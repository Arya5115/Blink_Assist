# Blink_Assist: Vision-based assistive communication for ALS / locked-in patients.

```
backend/   Django 4 + DRF + OpenCV + MediaPipe
frontend/  React 18 + Vite + TypeScript
```

This project uses a direct local/service deployment; Docker is not included.

## Telegram caregiver alerts
### Disclaimer: Creation of telegram bot is free of cost!
Only explicit caregiver alerts are sent to Telegram: emergency escalation,
automatic Sleep Mode and camera-loss transitions, and a patient's “Call
Caregiver” request. Routine blink/activity events remain in BlinkAssist and are
not forwarded. Telegram is the main stream alert service as of now, websockets or whatsapp chat bot can be integrated for much more reliable communication.

1. Create a bot with Telegram's [@BotFather](https://t.me/BotFather) and copy
	its bot token and username.
2. Copy `backend/.env.example` to `backend/.env`, then set
	`TELEGRAM_BOT_TOKEN` and `TELEGRAM_BOT_USERNAME` (without `@`). Do not
	commit the token.
3. Apply backend migrations and start Django as usual.
4. In a third backend terminal, start `python manage.py poll_telegram`.
5. Sign in to BlinkAssist as a caregiver, choose **Connect Telegram**, open the
	generated Telegram link, and press **Start**. Return to BlinkAssist and
	refresh the connection status.

Caregivers must have Telegram installed, start the bot, and enable Telegram
notifications. The polling command must remain running for Telegram account
linking; alert delivery itself uses Telegram's Bot API from Django.

## Quick start

```powershell
# Terminal 1 - backend
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
export ARDUINO_PORT=/dev/ttyUSB0
export ARDUINO_BUADRATE=115200
python manage.py runserver 0.0.0.0:8000

# Terminal 2 - frontend
cd frontend
npm install
npm run dev
# open http://localhost:5173

# Terminal 3 - backend
cd backend
python manage.py poll_telegram
```

