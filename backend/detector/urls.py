from django.urls import path
from . import views
urlpatterns = [
    path("health/", views.health),
    path("detect/", views.detect_frame),   # POST base64 image -> EAR + blink event
    path("reset/",  views.reset_state),
]
