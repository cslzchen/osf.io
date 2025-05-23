'''Unit tests for models and their factories.'''
import unittest
import pytest
from django.core.exceptions import ValidationError

from osf.models import RegistrationSchema
from osf.utils.migrations import ensure_schemas
from website.project.metadata.schemas import OSF_META_SCHEMA_FILES
from tests.base import OsfTestCase


@pytest.mark.enable_implicit_clean
class TestMetaData(OsfTestCase):

    def test_ensure_schemas(self):

        # Should be zero RegistrationSchema records to begin with
        RegistrationSchema.objects.all().delete()
        assert RegistrationSchema.objects.all().count() == 0
        ensure_schemas()

        assert RegistrationSchema.objects.all().count() == len(OSF_META_SCHEMA_FILES)

    def test_reigstrationschema_uniqueness_is_enforced_in_the_database(self):
        RegistrationSchema(name='foo', schema={'foo': 42}, schema_version=1).save()
        pytest.raises(ValidationError, RegistrationSchema(name='foo', schema={'bar': 24}, schema_version=1).save)

    def test_registrationschema_is_fine_with_same_name_but_different_version(self):
        RegistrationSchema(name='foo', schema={'foo': 42}, schema_version=1).save()
        RegistrationSchema(name='foo', schema={'foo': 42}, schema_version=2).save()
        assert RegistrationSchema.objects.filter(name='foo').count() == 2


if __name__ == '__main__':
    unittest.main()
