from django.urls import re_path
from .consumers import BlinkConsumer
websocket_urlpatterns = [re_path(r"^ws/blink/$", BlinkConsumer.as_asgi())]
