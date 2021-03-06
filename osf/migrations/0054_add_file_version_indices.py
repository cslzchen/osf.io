# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-09-11 18:14
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('osf', '0053_nodelog_faster_index'),
    ]

    operations = [
        migrations.RunSQL([
            'CREATE INDEX basefilenode_versions_compound_ids ON osf_basefilenode_versions (basefilenode_id, fileversion_id);',
            'CREATE INDEX fileversion_date_created_desc on osf_fileversion (date_created DESC);',
            # 'VACUUM ANALYZE osf_basefilenode_versions;'  # Run this manually, requires ~1 min downtime
            # 'VACUUM ANALYZE osf_fileversion;'  # Run this manually, requires ~2 min downtime
        ], [
            'DROP INDEX IF EXISTS basefilenode_versions_compound_ids RESTRICT;',
            'DROP INDEX IF EXISTS fileversion_date_created_desc RESTRICT;',
        ])
    ]
