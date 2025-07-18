import contextlib
from unittest import mock
import logging
import os
import re

from django.core.management import call_command
from django.db import transaction
from elasticsearch import exceptions as es_exceptions
from elasticsearch_dsl.connections import connections
from elasticsearch_metrics.registry import registry as es_metrics_registry
from faker import Factory
import pytest
import responses
import xml.etree.ElementTree as ET

from api_tests.share import _utils as shtrove_test_utils
from framework.celery_tasks import app as celery_app
from osf.external.spam import tasks as spam_tasks
from website import settings as website_settings


logger = logging.getLogger(__name__)

# Silence some 3rd-party logging and some "loud" internal loggers
SILENT_LOGGERS = [
    'api.caching.tasks',
    'factory.generate',
    'factory.containers',
    'framework.analytics',
    'framework.auth.core',
    'website.app',
    'website.archiver.tasks',
    'website.mails',
    'website.notifications.listeners',
    'website.search.elastic_search',
    'website.search_migration.migrate',
    'website.util.paths',
    'requests_oauthlib.oauth2_session',
    'transitions.core',
    'MARKDOWN',
    'elasticsearch',
]
for logger_name in SILENT_LOGGERS:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

@pytest.fixture(scope='session', autouse=True)
def override_settings():
    """Override settings for the test environment.
    """
    # Make tasks run synchronously, and make sure exceptions get propagated
    celery_app.conf.update({
        'task_always_eager': True,
        'task_eager_propagates': True,
    })
    website_settings.ENABLE_EMAIL_SUBSCRIPTIONS = False
    # TODO: Remove if this is unused?
    website_settings.BCRYPT_LOG_ROUNDS = 1
    # Make sure we don't accidentally send any emails
    website_settings.SENDGRID_API_KEY = None
    # or try to contact a SHARE
    website_settings.SHARE_ENABLED = False
    # Set this here instead of in SILENT_LOGGERS, in case developers
    # call setLevel in local.py
    logging.getLogger('website.mails.mails').setLevel(logging.CRITICAL)


@pytest.fixture()
def fake():
    return Factory.create()

_MOCKS = {
    'osf.models.user.new_bookmark_collection': {
        'mark': 'enable_bookmark_creation',
        'replacement': lambda *args, **kwargs: None,
    },
    'framework.celery_tasks.handlers._enqueue_task': {
        'mark': 'enable_enqueue_task',
        'replacement': lambda *args, **kwargs: None,
    },
    'osf.models.base.BaseModel.full_clean': {
        'mark': 'enable_implicit_clean',
        'replacement': lambda *args, **kwargs: None,
    },
    'osf.models.base._check_blacklist': {
        'mark': 'enable_blacklist_check',
        'replacement': lambda *args, **kwargs: False,
    },
    'website.search.search.search_engine': {
        'mark': 'enable_search',
        'replacement': mock.MagicMock()
    },
    'osf.external.messages.celery_publishers._publish_user_status_change': {
        'mark': 'enable_account_status_messaging',
        'replacement': mock.MagicMock()
    }
}

@pytest.fixture(autouse=True, scope='session')
def _test_speedups():
    mocks = {}

    for target, config in _MOCKS.items():
        mocks[target] = mock.patch(target, config['replacement'])
        mocks[target].start()

    yield mocks

    for patcher in mocks.values():
        patcher.stop()


@pytest.fixture(autouse=True)
def _test_speedups_disable(request, settings, _test_speedups):
    patchers = []
    for target, config in _MOCKS.items():
        if not request.node.get_closest_marker(config['mark']):
            continue
        patchers.append(_test_speedups[target])
        patchers[-1].stop()

    yield

    for patcher in patchers:
        patcher.start()


@pytest.fixture(scope='session')
def setup_connections():
    connections.create_connection(hosts=[website_settings.ELASTIC6_URI])


@pytest.fixture(scope='function')
def es6_client(setup_connections):
    return connections.get_connection()


@pytest.fixture(scope='function', autouse=True)
def _es_metrics_marker(request, worker_id):
    """Clear out all indices and index templates before and after
    tests marked with `es_metrics`.
    """
    marker = request.node.get_closest_marker('es_metrics')
    if marker:
        es6_client = request.getfixturevalue('es6_client')
        _temp_prefix = 'temp_metrics_'
        _temp_wildcard = f'{_temp_prefix}-{worker_id}*'

        def _teardown_es_temps():
            es6_client.indices.delete(index=_temp_wildcard)
            try:
                es6_client.indices.delete_template(_temp_wildcard)
            except es_exceptions.NotFoundError:
                pass

        @contextlib.contextmanager
        def _mock_metric_names():
            with contextlib.ExitStack() as _exit:
                for _metric_class in es_metrics_registry.get_metrics():
                    _exit.enter_context(mock.patch.object(
                        _metric_class,
                        '_template_name',  # also used to construct index names
                        f'{_temp_prefix}-{worker_id}{_metric_class._template_name}',
                    ))
                    _exit.enter_context(mock.patch.object(
                        _metric_class,
                        '_template',  # a wildcard string for indexes and templates
                        f'{_temp_prefix}-{worker_id}{_metric_class._template}',
                    ))
                yield

        _teardown_es_temps()
        with _mock_metric_names():
            call_command('sync_metrics')
            yield
        _teardown_es_temps()
    else:
        yield


@pytest.fixture
def mock_share_responses():
    with shtrove_test_utils.mock_share_responses() as _shmock_responses:
        yield _shmock_responses


@pytest.fixture
def mock_update_share():
    with shtrove_test_utils.mock_update_share() as _shmock_update:
        yield _shmock_update


@pytest.fixture
def mock_akismet():
    """
    This should be used to mock our anti-spam service akismet.
    Relevant endpoints:
    f'https://{api_key}.rest.akismet.com/1.1/submit-spam'
    f'https://{api_key}.rest.akismet.com/1.1/submit-ham'
    f'https://{api_key}.rest.akismet.com/1.1/comment-check'
    """
    with mock.patch.object(website_settings, 'SPAM_SERVICES_ENABLED', True):
        with mock.patch.object(website_settings, 'AKISMET_ENABLED', True):
            with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                rsps.add(responses.POST, 'https://test.crossref.org/servlet/deposit', status=200)
                yield rsps


@pytest.fixture
def mock_datacite(registration):
    """
    This should be used to mock our our datacite client.
    Relevant endpoints:
    f'{DATACITE_URL}/metadata'
    f'{DATACITE_URL}/doi'
    f'{DATACITE_URL}/metadata/{doi}'
    Datacite url should be `https://mds.datacite.org' for production and `https://mds.test.datacite.org` for local
    testing
    """

    doi = registration.get_doi_client().build_doi(registration)

    with open(os.path.join('tests', 'identifiers', 'fixtures', 'datacite_post_metadata_response.xml')) as fp:
        base_xml = ET.fromstring(fp.read())
        base_xml.find('{http://datacite.org/schema/kernel-4}identifier').text = doi
        data = ET.tostring(base_xml)

    with mock.patch.object(website_settings, 'DATACITE_ENABLED', True):
        with mock.patch.object(website_settings, 'DATACITE_USERNAME', 'TestDataciteUsername'):
            with mock.patch.object(website_settings, 'DATACITE_PASSWORD', 'TestDatacitePassword'):
                with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                    rsps.add(responses.GET, f'{website_settings.DATACITE_URL}/metadata', body=data, status=200)
                    rsps.add(responses.POST, f'{website_settings.DATACITE_URL}/metadata', body=f'OK ({doi})', status=201)
                    rsps.add(responses.POST, f'{website_settings.DATACITE_URL}/doi', body=f'OK ({doi})', status=201)
                    rsps.add(responses.DELETE, f'{website_settings.DATACITE_URL}/metadata/{doi}', status=200)
                    yield rsps


@pytest.fixture
def mock_crossref():
    """
    This should be used to mock our our crossref integration.
    Relevant endpoints:
    """
    with mock.patch.object(website_settings, 'CROSSREF_URL', 'https://test.crossref.org/servlet/deposit'):
        with mock.patch.object(website_settings, 'CROSSREF_USERNAME', 'TestCrossrefUsername'):
            with mock.patch.object(website_settings, 'CROSSREF_PASSWORD', 'TestCrossrefPassword'):
                with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                    rsps.add(responses.POST, website_settings.CROSSREF_URL, status=200)
                    yield rsps


@pytest.fixture
def mock_oopspam():
    """
    This should be used to mock our anti-spam service oopspam.
    Relevent endpoints:
    'https://oopspam.p.rapidapi.com/v1/spamdetection'
    """
    with mock.patch.object(website_settings, 'SPAM_SERVICES_ENABLED', True):
        with mock.patch.object(website_settings, 'OOPSPAM_ENABLED', True):
            with mock.patch.object(website_settings, 'OOPSPAM_APIKEY', 'FFFFFF'):
                with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                    yield rsps


@pytest.fixture
def mock_pigeon():
    """
    This should be used to mock our Internet Archive archiving microservice osf-pigeon.
    Relevent endpoints:
    '{settings.OSF_PIGEON_URL}archive/{guid}'
    '{settings.OSF_PIGEON_URL}metadata/{guid}'

    """
    def request_callback(request):
        guid = request.url.split('/')[-1]
        from osf.models import Registration
        reg = Registration.load(guid)
        reg.ia_url = 'https://test.ia.url.com'
        reg.save()
        return (200, {}, None)

    with mock.patch.object(website_settings, 'IA_ARCHIVE_ENABLED', True):
        with mock.patch.object(website_settings, 'OSF_PIGEON_URL', 'http://test.pigeon.osf.io/'):
            with mock.patch('osf.external.internet_archive.tasks.settings.OSF_PIGEON_URL', 'http://test.pigeon.osf.io/'):
                with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
                    rsps.add_callback(
                        method=responses.POST,
                        url=re.compile(f'{website_settings.OSF_PIGEON_URL}archive/(.*)'),
                        callback=request_callback
                    )
                    rsps.add(
                        method=responses.POST,
                        url=re.compile(f'{website_settings.OSF_PIGEON_URL}metadata/(.*)'),
                        status=200
                    )
                    yield rsps

@pytest.fixture
def mock_celery():
    """
    This should only be necessary for postcommit tasks.
    """
    with mock.patch.object(website_settings, 'USE_CELERY', True):
        with mock.patch('osf.external.internet_archive.tasks.enqueue_postcommit_task') as mock_celery:
            yield mock_celery


@pytest.fixture
def mock_spam_head_request():
    with mock.patch.object(spam_tasks.requests, 'head') as mock_spam_head_request:
        yield mock_spam_head_request


def rolledback_transaction(loglabel):
    class ExpectedRollback(Exception):
        pass
    try:
        with transaction.atomic():
            logger.debug(f'{loglabel}: started transaction')
            yield
            raise ExpectedRollback('this is an expected rollback; all is well')
    except ExpectedRollback:
        logger.debug(f'{loglabel}: rolled back transaction (as planned)')
    else:
        raise ExpectedRollback('expected a rollback but did not get one; something is wrong')


@pytest.fixture(scope='class')
def _class_scoped_db(django_db_setup, django_db_blocker):
    """a class-scoped version of the `django_db` mark
    (so we can use class-scoped fixtures to set up data
    for use across several tests)

    recommend using via the `with_class_scoped_db` fixture
    (so each function gets a nested transaction) instead of
    referencing directly.
    """
    with django_db_blocker.unblock():
        yield from rolledback_transaction('class_transaction')


@pytest.fixture(scope='function')
def with_class_scoped_db(_class_scoped_db):
    """wrap each function and the entire class in transactions
    (so fixtures can have scope='class' for reuse across tests,
    but what happens in each test stays in that test)

    example usage:
    ```
    @pytest.mark.usefixtures('with_class_scoped_db')
    class TestMyStuff:
        @pytest.fixture(scope='class')
        def helpful_thing(self):
            return HelpfulThingFactory()
    ```
    """
    yield from rolledback_transaction('function_transaction')

@pytest.fixture
def mock_gravy_valet_get_verified_links():
    """This fixture is used to mock a GV request which is made during node's identifier update. More specifically, when
    the tree walker in datacite metadata building process asks GV for verified links. As a result, this request must be
    mocked in many tests. The following decoration can be applied to either a test class or individual test methods.

    ```
    @pytest.mark.usefixtures('mock_gravy_valet_get_verified_links')
    ```
    """
    with mock.patch('osf.external.gravy_valet.translations.get_verified_links') as mock_get_verified_links:
        mock_get_verified_links.return_value = []
        yield mock_get_verified_links
