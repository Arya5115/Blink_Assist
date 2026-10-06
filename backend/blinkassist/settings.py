"""
Django settings for BlinkAssist (Objectives 1 & 2).
Minimal config: REST API + Channels (WebSocket) + CORS for the React (Vite) frontend.
"""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Local credentials live in backend/.env (which is git-ignored).  This avoids
# relying on the terminal that happened to start Django to retain Twilio values.
env_file = BASE_DIR / ".env"
if env_file.exists():
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-production")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "channels",
    "detector",
    "assist_platform",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "blinkassist.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

ASGI_APPLICATION = "blinkassist.asgi.application"
WSGI_APPLICATION = "blinkassist.wsgi.application"

# Use RedisChannelLayer in a multi-worker deployment. In-memory is retained for
# the no-Docker local setup and must not be used with multiple ASGI processes.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = False

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
}
