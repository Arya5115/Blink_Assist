from django.db import migrations


def assign_existing_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Patient = apps.get_model("assist_platform", "Patient")
    Caregiver = apps.get_model("assist_platform", "Caregiver")
    patient_group, _ = Group.objects.get_or_create(name="Patient")
    caregiver_group, _ = Group.objects.get_or_create(name="Caregiver")
    for profile in Patient.objects.all():
        profile.user.groups.add(patient_group)
    for profile in Caregiver.objects.all():
        profile.user.groups.add(caregiver_group)


class Migration(migrations.Migration):
    dependencies = [("assist_platform", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(assign_existing_roles, migrations.RunPython.noop)]
