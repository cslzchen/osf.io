# Generated by Django 4.2.15 on 2024-10-31 17:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0023_preprint_affiliated_institutions'),
    ]

    operations = [
        migrations.AddField(
            model_name='guid',
            name='is_versioned_base',
            field=models.BooleanField(default=False),
        ),
    ]
