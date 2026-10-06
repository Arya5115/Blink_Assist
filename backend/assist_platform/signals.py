from django.contrib.auth.models import Group
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Caregiver, Patient


def assign_group(user, name):
    group, _ = Group.objects.get_or_create(name=name)
    user.groups.add(group)


@receiver(post_save, sender=Patient)
def assign_patient_role(sender, instance, created, **kwargs):
    assign_group(instance.user, "Patient")


@receiver(post_save, sender=Caregiver)
def assign_caregiver_role(sender, instance, created, **kwargs):
    assign_group(instance.user, "Caregiver")
