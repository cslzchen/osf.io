import logging
import time

from addons.boa import utils

from boaapi.boa_client import BoaClient, BOA_API_ENDPOINT
from boaapi.status import CompilerStatus, ExecutionStatus

logger = logging.getLogger(__name__)


def submit_to_boa():

    client = BoaClient(endpoint=BOA_API_ENDPOINT)
    client.login(utils.test_username, utils.test_password)
    logger.info('Login successful')
    data_set = client.get_dataset(utils.test_data_set)
    job = client.query(utils.test_query, data_set)
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
