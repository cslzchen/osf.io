import pytest


from framework.auth import tasks
from osf_tests.factories import UserFactory
from tests.base import fake


class TestInstitutionAffiliationViaOrcidSso:

    @pytest.fixture()
    def verified_orcid_id(self):
        return fake.ean()

    @pytest.fixture()
    def pending_orcid_id(self):
        return fake.ean()

    @pytest.fixture()
    def user_with_verified_orcid_id(self, verified_orcid_id):
        return UserFactory(external_identity={'orcid': {verified_orcid_id: 'VERIFIED'}})

    @pytest.fixture()
    def user_with_pending_orcid_id(self):
        return UserFactory(external_identity={'orcid': {fake.ean(): 'LINK'}})

    @pytest.fixture()
    def user_without_orcid_id(self):
        pass

    def test_verify_user_orcid_id(
            self,
            verified_orcid_id,
            user_with_verified_orcid_id,
            pending_orcid_id,
            user_with_pending_orcid_id
    ):
        assert tasks.verify_user_orcid_id(user_with_verified_orcid_id, verified_orcid_id)
        assert not tasks.verify_user_orcid_id(user_with_pending_orcid_id, pending_orcid_id)


