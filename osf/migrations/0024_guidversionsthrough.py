# Generated by Django 4.2.15 on 2024-11-06 18:41

from django.db import migrations, models
import django.db.models.deletion
import django_extensions.db.fields
import osf.models.base


def migrate_preprints_single(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', "ContentType")
    Preprint = apps.get_model("osf", "Preprint")

    content_type_id = ContentType.objects.get_for_model(Preprint).id

    GUID = apps.get_model("osf", "GUID")

    guids = GUID.objects.filter(content_type_id=content_type_id)
    for guid in guids:
        if not guid.versions.exists():
            guid.versions.create(object_id=guid.object_id, version=1, content_type_id=content_type_id)


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('osf', '0023_preprint_affiliated_institutions'),
    ]

    operations = [
        migrations.CreateModel(
            name='GuidVersionsThrough',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('object_id', models.PositiveIntegerField(blank=True, null=True)),
                ('version', models.PositiveIntegerField(blank=True, null=True)),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('guid', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='osf.guid')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model, osf.models.base.QuerySetExplainMixin),
        ),
        # migrations.RunPython(migrate_preprints_single),
    ]
