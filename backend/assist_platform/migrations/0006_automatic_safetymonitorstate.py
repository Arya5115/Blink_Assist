from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("assist_platform", "0005_safetymonitorstate")]

    operations = [
        migrations.RenameField(
            model_name="safetymonitorstate",
            old_name="sleep_mode_armed",
            new_name="sleep_mode_on",
        ),
        migrations.RemoveField(
            model_name="safetymonitorstate",
            name="sleep_alerted",
        ),
    ]
