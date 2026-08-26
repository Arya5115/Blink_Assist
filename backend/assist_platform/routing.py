from django.urls import re_path
from .consumers import PlatformConsumer

# Use a single regex escape.  The previous ``\\d`` matched a literal backslash
# followed by "d", so every browser connection to /ws/patient/<id>/ was rejected.
websocket_urlpatterns = [re_path(r"^ws/patient/(?P<patient_id>\d+)/$", PlatformConsumer.as_asgi())]
