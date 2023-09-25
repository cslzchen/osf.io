import logging
import time

from . import settings

from boaapi.boa_client import BoaClient, BOA_API_ENDPOINT
from boaapi.status import CompilerStatus, ExecutionStatus

logger = logging.getLogger(__name__)


# Currently using pre-defined user credentials and fixtures from addon's local.py.
def submit_to_boa(user_settings=None, api_query=None, target_data_set=None):

    user_settings = settings.user_settings
    api_query = settings.test_query
    target_data_set = settings.test_data_set

    client = BoaClient(endpoint=BOA_API_ENDPOINT)
    client.login(user_settings['username'], user_settings['password'])
    logger.info('Login successful')
    data_set = client.get_dataset(target_data_set)
    job = client.query(api_query, data_set)
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
