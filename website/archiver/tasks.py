import requests
import json

from furl import furl
from rest_framework import status as http_status

import celery
from celery.utils.log import get_task_logger

from framework.celery_tasks import app as celery_app
from framework.celery_tasks.utils import logged
from framework.exceptions import HTTPError
from framework import sentry

from api.base.utils import waterbutler_api_url_for
from api.waffle.utils import flag_is_active

from website.archiver import (
    ARCHIVER_SUCCESS,
    ARCHIVER_FAILURE,
    ARCHIVER_SIZE_EXCEEDED,
    ARCHIVER_NETWORK_ERROR,
    ARCHIVER_FILE_NOT_FOUND,
    ARCHIVER_UNCAUGHT_ERROR,
    NO_ARCHIVE_LIMIT,
    AggregateStatResult,
)
from website.archiver import utils
from website.archiver.utils import normalize_unicode_filenames
from website.archiver import signals as archiver_signals

from website.project import signals as project_signals
from website import settings
from website.app import init_addons
from osf.models import (
    ArchiveJob,
    AbstractNode,
    DraftRegistration,
)
from osf import features
from osf.utils.requests import get_current_request
from osf.external.gravy_valet import request_helpers, translations


def create_app_context():
    try:
        init_addons(settings)
    except AssertionError:  # ignore AssertionErrors
        pass


logger = get_task_logger(__name__)

class ArchiverSizeExceeded(Exception):

    def __init__(self, result):
        self.result = result
        super().__init__(result)


class ArchiverStateError(Exception):

    def __init__(self, info):
        self.info = info
        super().__init__(info)


class ArchivedFileNotFound(Exception):

    def __init__(self, registration, missing_files):
        self.draft_registration = DraftRegistration.objects.get(registered_node=registration)
        self.missing_files = missing_files
        super().__init__(registration, missing_files)


class ArchiverTask(celery.Task):
    abstract = True
    max_retries = 0
    ignore_result = False

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        job = ArchiveJob.load(kwargs.get('job_pk'))
        if not job:
            archiver_state_exc = ArchiverStateError({
                'exception': exc,
                'args': args,
                'kwargs': kwargs,
                'einfo': einfo,
            })
            sentry.log_exception(archiver_state_exc)
            raise archiver_state_exc

        if job.status == ARCHIVER_FAILURE:
            # already captured
            return

        src, dst, user = job.info()
        errors = []
        if isinstance(exc, ArchiverSizeExceeded):
            dst.archive_status = ARCHIVER_SIZE_EXCEEDED
            errors = exc.result
        elif isinstance(exc, HTTPError):
            dst.archive_status = ARCHIVER_NETWORK_ERROR
            errors = [
                each for each in
                dst.archive_job.target_info()
                if each is not None
            ]
        elif isinstance(exc, ArchivedFileNotFound):
            dst.archive_status = ARCHIVER_FILE_NOT_FOUND
            errors = {
                'missing_files': exc.missing_files,
                'draft': exc.draft_registration
            }
        else:
            dst.archive_status = ARCHIVER_UNCAUGHT_ERROR
            errors = [str(einfo)] if einfo else []
        dst.save()

        sentry.log_message(
            'An error occured while archiving node',
            extra_data={
                'source node guid': src._id,
                'registration node guid': dst._id,
                'task_id': task_id,
                'errors': errors,
            },
        )

        archiver_signals.archive_fail.send(dst, errors=errors)

def get_addon_from_gv(src_node, addon_name, requesting_user):
    addon_data = request_helpers.get_addon(
        gv_addon_pk=f'{src_node._id}:{addon_name}',
        requested_resource=src_node,
        requesting_user=requesting_user,
        addon_type='configured-storage-addons'
    )
    return translations.make_ephemeral_node_settings(
        gv_addon_data=addon_data,
        requested_resource=src_node,
        requesting_user=requesting_user
    )


@celery_app.task(base=ArchiverTask, ignore_result=False)
@logged('stat_addon')
def stat_addon(addon_short_name, job_pk):
    """Collect metadata about the file tree of a given addon

    :param addon_short_name: AddonConfig.short_name of the addon to be examined
    :param job_pk: primary key of archive_job
    :return: AggregateStatResult containing file tree metadata
    """
    # Dataverse reqires special handling for draft and
    # published content
    addon_name = addon_short_name
    version = None
    if 'dataverse' in addon_short_name:
        addon_name = 'dataverse'
        version = 'latest' if addon_short_name.split('-')[-1] == 'draft' else 'latest-published'
    create_app_context()
    job = ArchiveJob.load(job_pk)
    src, dst, user = job.info()

    src_addon = None
    if addon_name != 'osfstorage' and flag_is_active(get_current_request(), features.ENABLE_GV):
        src_addon = get_addon_from_gv(src, addon_name, user)
    else:
        src_addon = src.get_addon(addon_name)
    if hasattr(src_addon, 'configured') and not src_addon.configured:
        # Addon enabled but not configured - no file trees, nothing to archive.
        return AggregateStatResult(src_addon._id, addon_short_name)
    try:
        file_tree = src_addon._get_file_tree(user=user, version=version)
    except HTTPError as e:
        dst.archive_job.update_target(
            addon_short_name,
            ARCHIVER_NETWORK_ERROR,
            errors=[e.data['error']],
        )
        raise
    result = AggregateStatResult(
        src_addon._id,
        addon_short_name,
        targets=[utils.aggregate_file_tree_metadata(addon_short_name, file_tree, user)],
    )
    return result


@celery_app.task(base=ArchiverTask, ignore_result=False)
@logged('make_copy_request')
def make_copy_request(job_pk, url, data):
    """Make the copy request to the WaterButler API and handle
    successful and failed responses

    :param job_pk: primary key of ArchiveJob
    :param url: URL to send request to
    :param data: <dict> of setting to send in POST to WaterButler API
    :return: None
    """
    create_app_context()
    job = ArchiveJob.load(job_pk)
    src, dst, user = job.info()
    logger.info(f"Sending copy request for addon: {data['provider']} on node: {dst._id}")
    cookie = furl(url).query.params.get('cookie')
    res = requests.post(url, data=json.dumps(data), cookies={settings.COOKIE_NAME: cookie})
    if res.status_code not in (http_status.HTTP_200_OK, http_status.HTTP_201_CREATED, http_status.HTTP_202_ACCEPTED):
        raise HTTPError(res.status_code)

def make_waterbutler_payload(dst_id, rename):
    return {
        'action': 'copy',
        'path': '/',
        'rename': rename.replace('/', '-'),
        'resource': dst_id,
        'provider': settings.ARCHIVE_PROVIDER,
    }

@celery_app.task(base=ArchiverTask, ignore_result=False)
@logged('archive_addon')
def archive_addon(addon_short_name, job_pk):
    """Archive the contents of an addon by making a copy request to the
    WaterButler API

    :param addon_short_name: AddonConfig.short_name of the addon to be archived
    :param job_pk: primary key of ArchiveJob
    :return: None
    """
    create_app_context()
    job = ArchiveJob.load(job_pk)
    src, dst, user = job.info()
    logger.info(f'Archiving addon: {addon_short_name} on node: {src._id}')

    cookie = user.get_or_create_cookie().decode()
    params = {'cookie': cookie}
    rename_suffix = ''
    # The dataverse API will not differentiate between published and draft files
    # unless expcicitly asked. We need to create seperate folders for published and
    # draft in the resulting archive.
    #
    # Additionally trying to run the archive without this distinction creates a race
    # condition that non-deterministically caused archive jobs to fail.
    if 'dataverse' in addon_short_name:
        params['revision'] = 'latest' if addon_short_name.split('-')[-1] == 'draft' else 'latest-published'
        rename_suffix = ' (draft)' if addon_short_name.split('-')[-1] == 'draft' else ' (published)'
        addon_short_name = 'dataverse'
    src_provider = None
    if addon_short_name != 'osfstorage' and flag_is_active(get_current_request(), features.ENABLE_GV):
        src_provider = get_addon_from_gv(src, addon_short_name, user)
    else:
        src_provider = src.get_addon(addon_short_name)
    folder_name_nfd, folder_name_nfc = normalize_unicode_filenames(src_provider.archive_folder_name)
    rename = f'{folder_name_nfd}{rename_suffix}'
    url = waterbutler_api_url_for(src._id, addon_short_name, _internal=True, base_url=src.osfstorage_region.waterbutler_url, **params)
    data = make_waterbutler_payload(dst._id, rename)
    make_copy_request.delay(job_pk=job_pk, url=url, data=data)

@celery_app.task(base=ArchiverTask, ignore_result=False)
@logged('archive_node')
def archive_node(stat_results, job_pk):
    """First use the results of #stat_node to check disk usage of the
    initiated registration, then either fail the registration or
    create a celery.group group of subtasks to archive addons

    :param results: results from the #stat_addon subtasks spawned in #stat_node
    :param job_pk: primary key of ArchiveJob
    :return: None
    """
    create_app_context()
    job = ArchiveJob.load(job_pk)
    src, dst, user = job.info()
    logger.info(f'Archiving node: {src._id}')

    if not isinstance(stat_results, list):
        stat_results = [stat_results]
    stat_result = AggregateStatResult(
        dst._id,
        dst.title,
        targets=stat_results
    )
    if (NO_ARCHIVE_LIMIT not in job.initiator.system_tags) and (stat_result.disk_usage > settings.MAX_ARCHIVE_SIZE):
        raise ArchiverSizeExceeded(result=stat_result)
    else:
        if not stat_result.targets:
            job.status = ARCHIVER_SUCCESS
            job.save()
        for result in stat_result.targets:
            if not result['num_files']:
                job.update_target(result['target_name'], ARCHIVER_SUCCESS)
            else:
                archive_addon.delay(
                    addon_short_name=result['target_name'],
                    job_pk=job_pk
                )
        project_signals.archive_callback.send(dst)


def archive(job_pk):
    """Starts a celery.chord that runs stat_addon for each
    complete addon attached to the Node, then runs
    #archive_node with the result

    :param job_pk: primary key of ArchiveJob
    :return: None
    """
    create_app_context()
    job = ArchiveJob.load(job_pk)
    src, dst, user = job.info()
    logger = get_task_logger(__name__)
    logger.info(f'Received archive task for Node: {src._id} into Node: {dst._id}')
    return celery.chain(
        [
            celery.group([
                stat_addon.si(
                    addon_short_name=target.name,
                    job_pk=job_pk,
                )
                for target in job.target_addons.all()
            ]),
            archive_node.s(
                job_pk=job_pk
            )
        ]
    )


@celery_app.task(base=ArchiverTask, ignore_result=False)
@logged('archive_success')
def archive_success(dst_pk, job_pk):
    """Archiver's final callback. For the time being the use case for this task
    is to rewrite references to files selected in a registration schema (the Prereg
    Challenge being the first to expose this feature). The created references point
    to files on the registered_from Node (needed for previewing schema data), and
    must be re-associated with the corresponding files in the newly created registration.

    :param str dst_pk: primary key of registration Node

    note:: At first glance this task makes redundant calls to utils.get_file_map (which
    returns a generator yielding  (<sha256>, <file_metadata>) pairs) on the dst Node. Two
    notes about utils.get_file_map: 1) this function memoizes previous results to reduce
    overhead and 2) this function returns a generator that lazily fetches the file metadata
    of child Nodes (it is possible for a selected file to belong to a child Node) using a
    non-recursive DFS. Combined this allows for a relatively effient implementation with
    seemingly redundant calls.
    """
    create_app_context()
    dst = AbstractNode.load(dst_pk)

    # Cache registration files count
    dst.update_files_count()

    # Update file references in the Registration's responses to point to the archived
    # file on the Registration instead of the "live" version on the backing project
    utils.migrate_file_metadata(dst)
    job = ArchiveJob.load(job_pk)
    if not job.sent:
        job.sent = True
        job.save()
        dst.sanction.ask(dst.get_active_contributors_recursive(unique_users=True))

    dst.update_search()
