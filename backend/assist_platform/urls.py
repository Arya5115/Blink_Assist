from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import AccountManagementViewSet, ApplianceViewSet, CalibrationViewSet, CommunicationViewSet, DatasetSessionViewSet, EventViewSet, NotificationViewSet, PatientViewSet, SafetyViewSet, register_account

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("events", EventViewSet, basename="event")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("communications", CommunicationViewSet, basename="communication")
router.register("calibrations", CalibrationViewSet, basename="calibration")
router.register("safety", SafetyViewSet, basename="safety")
router.register("appliances", ApplianceViewSet, basename="appliance")
router.register("datasets", DatasetSessionViewSet, basename="dataset")
router.register("accounts", AccountManagementViewSet, basename="account")
urlpatterns = router.urls + [path("auth/register/", register_account, name="register-account")]
