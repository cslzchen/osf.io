# This is a management command, rather than a migration script, for two primary reasons:
#   1. It makes no changes to database structure (e.g. AlterField), only database content.
#   2. It takes a long time to run and the site doesn't need to be down that long.

import logging


import django
django.setup()

from django.core.management.base import BaseCommand
from framework import sentry

from website import mails

from osf.models import OSFUser

logger = logging.getLogger(__name__)

OFFSET = 500000

def email_all_users(email_template, dry_run=False, ids=None, start_id=0, offset=OFFSET):

    if ids:
        active_users = OSFUser.objects.filter(id__in=ids)
    else:
        lower_bound = start_id
        upper_bound = start_id + offset
        base_query = OSFUser.objects.filter(date_confirmed__isnull=False, deleted=None).exclude(date_disabled__isnull=False).exclude(is_active=False)
        active_users = base_query.filter(id__gt=lower_bound, id__lte=upper_bound).order_by('id')

    if dry_run:
        active_users = active_users.exclude(is_superuser=False)

    total_active_users = active_users.count()

    logger.info(f'About to send an email to {total_active_users} users.')

    template = getattr(mails, email_template, None)
    if not template:
        raise RuntimeError('Invalid email template specified!')

    total_sent = 0
    for user in active_users.iterator():
        logger.info(f'Sending email to {user.id}')
        try:
            mails.send_mail(
                to_addr=user.email,
                mail=template,
                given_name=user.given_name or user.fullname,
            )
        except Exception as e:
            logger.error(f'Exception encountered sending email to {user.id}')
            sentry.log_exception(e)
            continue
        else:
            total_sent += 1

    logger.info(f'Emails sent to {total_sent}/{total_active_users} users')


class Command(BaseCommand):
    """
    Add subscription to all active users for given notification type.
    """
    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--dry',
            action='store_true',
            dest='dry_run',
            help='Test - Only send to superusers'
        )

        parser.add_argument(
            '--t',
            type=str,
            dest='template',
            required=True,
            help='Specify which template to use'
        )

        parser.add_argument(
            '--start-id',
            type=int,
            dest='start_id',
            default=0,
            help='Specify id to start from.'
        )

        parser.add_argument(
            '--ids',
            dest='ids',
            nargs='+',
            help='Specific IDs to email, otherwise will email all users'
        )

        parser.add_argument(
            '--o',
            type=int,
            dest='offset',
            default=OFFSET,
            help=f'How many users to email in this run, default is {OFFSET}'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        template = options.get('template')
        start_id = options.get('start_id')
        ids = options.get('ids')
        offset = options.get('offset', OFFSET)
        email_all_users(template, dry_run, start_id=start_id, ids=ids, offset=offset)
        if dry_run:
            raise RuntimeError('Dry run, only superusers emailed')
