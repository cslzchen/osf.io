#!/usr/bin/env python3
"""Generate a sitemap for osf.io"""
import boto3
import datetime
import gzip
import os
import shutil
from urllib.parse import urlparse, urljoin
import xml

import django
from django.contrib.sitemaps.views import sitemap

django.setup()
import logging
import tempfile

from framework import sentry
from framework.celery_tasks import app as celery_app
from django.db.models import Q, F, OuterRef, Subquery
from osf.models import OSFUser, AbstractNode, Preprint, PreprintProvider
from osf.utils.workflows import DefaultStates
from scripts import utils as script_utils
from website import settings
from website.app import init_app

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Sitemap:
    def __init__(self):
        self.sitemap_count = 0
        self.errors = 0
        self.new_doc()
        if not settings.SITEMAP_TO_S3:
            self.sitemap_dir = os.path.join(settings.STATIC_FOLDER, 'sitemaps')
            if not os.path.exists(self.sitemap_dir):
                print(f'Creating sitemap directory at `{self.sitemap_dir}`')
                os.makedirs(self.sitemap_dir)
        else:
            self.sitemap_dir = tempfile.mkdtemp()
            assert settings.SITEMAP_AWS_BUCKET, 'SITEMAP_AWS_BUCKET must be set for sitemap files to be sent to S3'
            assert settings.AWS_ACCESS_KEY_ID, 'AWS_ACCESS_KEY_ID must be set for sitemap files to be sent to S3'
            assert settings.AWS_SECRET_ACCESS_KEY, 'AWS_SECRET_ACCESS_KEY must be set for sitemap files to be sent to S3'
            self.s3 = boto3.resource(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name='us-east-1'
            )

    def cleanup(self):
        if settings.SITEMAP_TO_S3:
            shutil.rmtree(self.sitemap_dir)

    def new_doc(self):
        """Creates new sitemap document and resets the url_count."""
        self.doc = xml.dom.minidom.Document()
        self.urlset = self.doc.createElement('urlset')
        self.urlset.setAttribute('xmlns', 'http://www.sitemaps.org/schemas/sitemap/0.9')
        self.doc.appendChild(self.urlset)
        self.url_count = 0

    def add_tag(self, name, text):
        """Adds a tag to the current url"""
        tag = self.doc.createElement(name)
        self.url.appendChild(tag)
        tag_text = self.doc.createTextNode(text)
        tag.appendChild(tag_text)

    def add_url(self, config):
        """Adds a url to the current urlset"""
        if self.url_count >= settings.SITEMAP_URL_MAX:
            self.write_doc()
            self.new_doc()
        self.url = self.doc.createElement('url')
        self.urlset.appendChild(self.url)

        for k, v in config.items():
            self.add_tag(k, v)
        self.url_count += 1

    def write_doc(self):
        """Writes and gzips each sitemap xml file"""
        file_name = f'sitemap_{str(self.sitemap_count)}.xml'
        file_path = os.path.join(self.sitemap_dir, file_name)
        zip_file_name = file_name + '.gz'
        zip_file_path = file_path + '.gz'
        print(f'Writing and gzipping `{file_path}`: url_count = {str(self.url_count)}')

        xml_str = self.doc.toprettyxml(indent='  ', encoding='utf-8')
        with open(file_path, 'wb') as f:
            f.write(xml_str)

        # Write zipped file
        with open(file_path, 'rb') as f_in, gzip.open(zip_file_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
        if settings.SITEMAP_TO_S3:
            self.ship_to_s3(file_name, file_path)
            self.ship_to_s3(zip_file_name, zip_file_path)
        self.sitemap_count += 1

    def ship_to_s3(self, name, path):
        data = open(path, 'rb')
        try:
            self.s3.Bucket(settings.SITEMAP_AWS_BUCKET).put_object(Key=f'sitemaps/{name}', Body=data)
        except Exception as e:
            logger.info('Error sending data to s3 via boto3')
            logger.exception(e)
            sentry.log_message('ERROR: Sitemaps could not be uploaded to s3, see `generate_sitemap` logs')
        data.close()

    def write_sitemap_index(self):
        """Writes the index file for all of the sitemap files"""
        doc = xml.dom.minidom.Document()
        sitemap_index = self.doc.createElement('sitemapindex')
        sitemap_index.setAttribute('xmlns', 'http://www.sitemaps.org/schemas/sitemap/0.9')
        doc.appendChild(sitemap_index)

        for f in range(self.sitemap_count):
            sitemap = doc.createElement('sitemap')
            sitemap_index.appendChild(sitemap)

            loc = doc.createElement('loc')
            sitemap.appendChild(loc)
            loc_text = self.doc.createTextNode(urljoin(settings.DOMAIN, f'sitemaps/sitemap_{str(f)}.xml'))
            loc.appendChild(loc_text)

            datemod = doc.createElement('lastmod')
            sitemap.appendChild(datemod)
            datemod_text = self.doc.createTextNode(datetime.datetime.now().strftime('%Y-%m-%d'))
            datemod.appendChild(datemod_text)

        print('Writing `sitemap_index.xml`')
        file_name = 'sitemap_index.xml'
        file_path = os.path.join(self.sitemap_dir, file_name)
        xml_str = doc.toprettyxml(indent='  ', encoding='utf-8')
        with open(file_path, 'wb') as f:
            f.write(xml_str)
        if settings.SITEMAP_TO_S3:
            self.ship_to_s3(file_name, file_path)

    def log_errors(self, obj, obj_id, error):
        if not self.errors:
            script_utils.add_file_logger(logger, __file__)
        self.errors += 1
        logger.info(f'Error on {obj}, {obj_id}:')
        logger.exception(error)

        if self.errors <= 10:
            sentry.log_message(f'Sitemap Error: {error}')

        if self.errors == 1000:
            sentry.log_message('ERROR: generate_sitemap stopped execution after reaching 1000 errors. See logs for details.')
            raise Exception('Too many errors generating sitemap.')

    def generate(self):
        print('Generating Sitemap')

        # Progress bar
        progress = script_utils.Progress(precision=0)

        # Static urls
        progress.start(len(settings.SITEMAP_STATIC_URLS), 'STAT: ')
        for config in settings.SITEMAP_STATIC_URLS:
            config['loc'] = urljoin(settings.DOMAIN, config['loc'])
            self.add_url(config)
            progress.increment()
        progress.stop()

        # User urls
        objs = OSFUser.objects.filter(is_active=True).exclude(date_confirmed__isnull=True).values_list('guids___id', flat=True)
        progress.start(objs.count(), 'USER: ')
        for obj in objs:
            try:
                config = settings.SITEMAP_USER_CONFIG
                config['loc'] = urljoin(settings.DOMAIN, f'/{obj}/')
                self.add_url(config)
            except Exception as e:
                self.log_errors('USER', obj, e)
            progress.increment()
        progress.stop()

        # AbstractNode urls (Nodes and Registrations, no Collections)
        objs = (AbstractNode.objects
            .filter(is_public=True, is_deleted=False, retraction_id__isnull=True)
            .exclude(type__in=['osf.collection', 'osf.quickfilesnode'])
            .values('guids___id', 'modified'))
        progress.start(objs.count(), 'NODE: ')
        for obj in objs:
            try:
                config = settings.SITEMAP_NODE_CONFIG
                config['loc'] = urljoin(settings.DOMAIN, '/{}/'.format(obj['guids___id']))
                config['lastmod'] = obj['modified'].strftime('%Y-%m-%d')
                self.add_url(config)
            except Exception as e:
                self.log_errors('NODE', obj['guids___id'], e)
            progress.increment()
        progress.stop()
        #Removed previous logic as it blocked withdrawn preprints to get in sitemap generator
        objs = Preprint.objects.filter(date_published__isnull=False).annotate(
            most_recent_non_withdrawn=Subquery(
                Preprint.objects.filter(
                    guids=OuterRef('guids')
                ).order_by('-versioned_guids__version').values('versioned_guids__version')[:1]
            )
        ).order_by(
            'versioned_guids__guid_id',
            '-is_published',
            '-versioned_guids__version'
        ).distinct('versioned_guids__guid_id')

        progress.start(len(objs) * 2, 'PREP: ')
        for obj in objs:
            try:
                preprint_date = obj.modified.strftime('%Y-%m-%d')
                config = settings.SITEMAP_PREPRINT_CONFIG
                preprint_url = os.path.join('preprints', obj.provider._id, obj._id)
                config['loc'] = urljoin(settings.DOMAIN, preprint_url)
                config['lastmod'] = preprint_date
                self.add_url(config)

                # Preprint file urls
                if not obj.is_retracted:
                    # Withdrawn preprints may be viewed but not downloaded
                    try:
                        file_config = settings.SITEMAP_PREPRINT_FILE_CONFIG
                        file_config['loc'] = urljoin(
                            settings.DOMAIN,
                            os.path.join(
                                obj._id,
                                'download',
                                '?format=pdf'
                            )
                        )
                        file_config['lastmod'] = preprint_date
                        self.add_url(file_config)
                    except Exception as e:
                        self.log_errors(obj.primary_file, obj.primary_file._id, e)

            except Exception as e:
                self.log_errors(obj, obj._id, e)
            progress.increment(2)
        progress.stop()

        # Final write
        self.write_doc()
        # Create index file
        self.write_sitemap_index()

        # TODO: once the sitemap is validated add a ping to google with sitemap index file location
        # TODO: server side cursor query wrapper might be useful as the index gets larger.
        # Sitemap indexable limit check
        if self.sitemap_count > settings.SITEMAP_INDEX_MAX * .90:  # 10% of urls remaining
            sentry.log_message('WARNING: Max sitemaps nearly reached.')
        print(f'Total url_count = {(self.sitemap_count - 1) * settings.SITEMAP_URL_MAX + self.url_count}')
        print(f'Total sitemap_count = {str(self.sitemap_count)}')
        if self.errors:
            sentry.log_message('WARNING: Generate sitemap encountered errors. See logs for details.')
            print(f'Total errors = {str(self.errors)}')
        else:
            print('No errors')

@celery_app.task(name='scripts.generate_sitemap')
def main():
    init_app(routes=False)  # Sets the storage backends on all models
    sitemap = Sitemap()
    sitemap.generate()
    sitemap.cleanup()

if __name__ == '__main__':
    init_app(set_backends=True, routes=False)
    main()
