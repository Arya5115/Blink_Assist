from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assist_platform", "0003_platform_v2_audit")]

    operations = [
        migrations.AddField(
            model_name="datasetframe",
            name="predicted_label",
            field=models.CharField(default="Noise", max_length=32),
        ),
    ]
