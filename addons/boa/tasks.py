import logging
from urllib import request
import time

from addons.boa import settings as boa_settings
from addons.osfstorage.models import OsfStorageFile
from api.files.serializers import get_file_download_link
from osf.models import OSFUser
from osf.utils.fields import ensure_str
from website import settings as osf_settings

from boaapi.boa_client import BoaClient, BoaException, BOA_API_ENDPOINT
from boaapi.status import CompilerStatus, ExecutionStatus

logger = logging.getLogger(__name__)


# Currently using pre-defined user credentials and fixtures from addon's local.py.
def submit_to_boa(file_guid, user_guid, target_data_set=None):

    user = OSFUser.objects.get(guids___id=user_guid)
    cookie_value = user.get_or_create_cookie().decode()
    # TODO: get target data set from submit view (legacy files widget and/or ember files page)
    # target_data_set = target_data_set
    logger.info(f'Downloading boa query file {file_guid}...')
    boa_file = OsfStorageFile.objects.get(guids___id=file_guid)
    file_download_link = get_file_download_link(boa_file)
    logger.info(f'File download link (domain): {file_download_link}')
    internal_link = file_download_link.replace(osf_settings.DOMAIN, osf_settings.INTERNAL_DOMAIN)
    logger.info(f'File download link (internal domain: {internal_link}')
    submit_request = request.Request(internal_link)
    submit_request.add_header('Cookie', f'{osf_settings.COOKIE_NAME}={cookie_value}')
    response = request.urlopen(submit_request)
    content = response.read()
    boa_query = ensure_str(content)
    logger.info(f'boa query downloaded:\n{boa_query}')

    # TODO: get user settings from DB
    user_settings = boa_settings.user_settings
    target_data_set = boa_settings.test_data_set

    client = BoaClient(endpoint=BOA_API_ENDPOINT)
    try:
        client.login(user_settings['username'], user_settings['password'])
    except BoaException:
        logger.error('Login failed!')
        client.close()
        return
    logger.info('Login successful')
    try:
        data_set = client.get_dataset(target_data_set)
    except BoaException:
        logger.error(f'Invalid data set: {target_data_set}!')
        client.close()
        return

    job = client.query(boa_query, data_set)
    logger.info('Query submitted')
    while job.is_running():
        job.refresh()
        logger.warning(f'Job {str(job.id)} still running, waiting 10s...')
        time.sleep(10)
    if job.compiler_status is CompilerStatus.ERROR:
        logger.error(f'Job {str(job.id)} failed with compile error')
    elif job.compiler_status is ExecutionStatus.ERROR:
        logger.error(f'Job {str(job.id)} failed with execution error')
    else:
        logger.info(f'Job {str(job.id)}:\n{job.output()}')

    client.close()
    return
