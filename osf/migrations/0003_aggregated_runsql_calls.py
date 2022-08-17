from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0002_adminlogentry'),
    ]

    operations = [
        migrations.RunSQL(
            [
                """
                CREATE UNIQUE INDEX osf_noderequest_target_creator_non_accepted ON osf_noderequest (target_id, creator_id)
                WHERE machine_state != 'accepted';
                """
            ], [
                """
                DROP INDEX IF EXISTS osf_noderequest_target_creator_non_accepted RESTRICT;
                """
            ]
        ),
    ]
