from rest_framework.exceptions import PermissionDenied

PATIENT = "patient"
CAREGIVER = "caregiver"
ADMIN = "admin"
UNASSIGNED = "unassigned"


def role_for(user):
    if user.is_superuser or user.is_staff:
        return ADMIN
    if user.groups.filter(name="Patient").exists():
        return PATIENT
    if user.groups.filter(name="Caregiver").exists():
        return CAREGIVER
    return UNASSIGNED


def require_patient_controller(request):
    """Only the authenticated patient (or an admin service user) can send commands."""
    role = role_for(request.user)
    if role not in (PATIENT, ADMIN):
        raise PermissionDenied("Only the patient account may control this device.")
    return role
