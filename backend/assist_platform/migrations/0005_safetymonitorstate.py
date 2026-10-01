from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("assist_platform", "0004_datasetframe_predicted_label")]

    operations = [
        migrations.CreateModel(
            name="SafetyMonitorState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sleep_mode_armed", models.BooleanField(default=False)),
                ("sleep_alerted", models.BooleanField(default=False)),
                ("camera_loss_alerted", models.BooleanField(default=False)),
                ("patient", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="safety_monitor_state", to="assist_platform.patient")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
