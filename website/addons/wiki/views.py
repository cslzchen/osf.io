# -*- coding: utf-8 -*-

import difflib
import httplib as http
import logging

from bs4 import BeautifulSoup
from flask import request

from framework.mongo.utils import from_mongo, to_mongo
from framework.exceptions import HTTPError
from framework.auth.utils import privacy_info_handle

from website.project.views.node import _view_project
from website.project import show_diff
from website.project.model import has_anonymous_link
from website.project.decorators import (
    must_be_contributor_or_public,
    must_have_addon, must_not_be_registration,
    must_be_valid_project,
    must_have_permission
)

from .model import NodeWikiPage

logger = logging.getLogger(__name__)


@must_be_contributor_or_public
@must_have_addon('wiki', 'node')
def wiki_widget(**kwargs):
    node = kwargs['node'] or kwargs['project']
    wiki = node.get_addon('wiki')
    wiki_page = node.get_wiki_page('home')

    more = False
    if wiki_page and wiki_page.html(node):
        wiki_html = wiki_page.html(node)
        if len(wiki_html) > 500:
            wiki_html = BeautifulSoup(wiki_html[:500] + '...', 'html.parser')
            more = True
        else:
            wiki_html = BeautifulSoup(wiki_html)
            more = False
    else:
        wiki_html = None

    rv = {
        'complete': True,
        'content': str(wiki_html),
        'more': more,
        'include': False,
    }
    rv.update(wiki.config.to_json())
    return rv


@must_be_valid_project
@must_have_addon('wiki', 'node')
def project_wiki_home(**kwargs):
    node = kwargs['node'] or kwargs['project']
    return {}, None, None, node.web_url_for('project_wiki_page', wid='home')


def _get_wiki_versions(node, wid, anonymous=False):
    wid_key = to_mongo(wid).lower()
    # Skip if page doesn't exist; happens on new projects before
    # default "home" page is created
    if wid_key not in node.wiki_pages_versions:
        return []

    versions = [
        NodeWikiPage.load(page)
        for page in node.wiki_pages_versions[wid_key]
    ]

    return [
        {
            'version': version.version,
            'user_fullname': privacy_info_handle(
                version.user.fullname, anonymous, name=True
            ),
            'date': version.date.replace(microsecond=0),
            'compare_web_url': node.web_url_for('project_wiki_compare', wid=wid, compare_id=version.version),
        }
        for version in reversed(versions)
    ]


def _get_wiki_pages_current(node):
    return [
        {
            'name': page,
            'url': node.web_url_for('project_wiki_page', wid=page)
        }
        for page in sorted([
            from_mongo(version)
            for version in node.wiki_pages_current
        ])
    ]


def _get_wiki_api_urls(node, wid, additional_urls=None):
    urls = {
        'delete': node.api_url_for('project_wiki_delete', wid=wid),
        'rename': node.api_url_for('project_wiki_rename', wid=wid),
    }
    if additional_urls:
        urls.update(additional_urls)
    return urls


def _get_wiki_web_urls(node, wid, compare_id=1, additional_urls=None):
    urls = {
        'base': node.web_url_for('project_wiki_home'),
        'compare': node.web_url_for('project_wiki_compare', wid=wid, compare_id=compare_id),
        'edit': node.web_url_for('project_wiki_edit', wid=wid),
        'home': node.web_url_for('project_wiki_home'),
        'page': node.web_url_for('project_wiki_page', wid=wid),
    }
    if additional_urls:
        urls.update(additional_urls)
    return urls


@must_be_valid_project  # injects project
@must_be_contributor_or_public  # injects user, project
@must_have_addon('wiki', 'node')
def project_wiki_compare(auth, wid, compare_id, **kwargs):
    node = kwargs['node'] or kwargs['project']

    anonymous = has_anonymous_link(node, auth)
    wiki_page = node.get_wiki_page(wid)
    toc = serialize_wiki_toc(node, auth=auth)

    if not wiki_page:
        raise HTTPError(http.NOT_FOUND)

    comparison_page = node.get_wiki_page(wid, compare_id)
    if comparison_page:
        current = wiki_page.content
        comparison = comparison_page.content
        sm = difflib.SequenceMatcher(None, comparison, current)
        content = show_diff(sm)
        content = content.replace('\n', '<br />')
        ret = {
            'wiki_id': wiki_page._primary_key if wiki_page else None,
            'wiki_name': wid,
            'wiki_content': content,
            'versions': _get_wiki_versions(node, wid, anonymous),
            'is_current': True,
            'is_edit': False,
            'version': wiki_page.version,
            'compare_id': compare_id,
            'pages_current': _get_wiki_pages_current(node),
            'toc': toc,
            'category': node.category,
            'urls': {
                'api': _get_wiki_api_urls(node, wid),
                'web': _get_wiki_web_urls(node, wid, compare_id),
            },
        }
        ret.update(_view_project(node, auth, primary=True))
        return ret

    raise HTTPError(http.NOT_FOUND)


@must_be_valid_project  # injects project
@must_have_permission('write')  # injects auth, project
@must_have_addon('wiki', 'node')
def project_wiki_version(wid, vid, auth, **kwargs):
    node = kwargs['node'] or kwargs['project']
    wiki_page = node.get_wiki_page(wid, version=vid)

    if wiki_page:
        rv = {
            'wiki_id': wiki_page._id if wiki_page else None,
            'wiki_name': wid,
            'wiki_content': wiki_page.html(node),
            'version': wiki_page.version,
            'is_current': wiki_page.is_current,
            'is_edit': False,
            'wiki_version_web_url': node.web_url_for('project_wiki_version', wid=wid, compare_id=vid),
        }
        rv.update(_view_project(node, auth, primary=True))
        return rv

    raise HTTPError(http.NOT_FOUND)


def serialize_wiki_toc(project, auth):
    toc = [
        {
            'id': child._primary_key,
            'title': child.title,
            'category': child.category,
            'pages_current': _get_wiki_pages_current(child),
            'url': child.web_url_for('project_wiki_page', wid='home'),
            'is_pointer': not child.primary,
            'link': auth.private_key
        }
        for child in project.nodes
        if not child.is_deleted
        and child.can_view(auth)
        if child.has_addon('wiki')
    ]
    return toc


@must_be_valid_project  # injects project
@must_be_contributor_or_public
@must_have_addon('wiki', 'node')
def project_wiki_page(wid, auth, **kwargs):
    wid = wid.strip()
    node = kwargs['node'] or kwargs['project']
    anonymous = has_anonymous_link(node, auth)
    wiki_page = node.get_wiki_page(wid)

    # todo breaks on /<script>; why?

    if wiki_page:
        version = wiki_page.version
        is_current = wiki_page.is_current
        content = wiki_page.html(node)
    else:
        version = 'NA'
        is_current = False
        content = '<p><em>No wiki content</em></p>'

    toc = serialize_wiki_toc(node, auth=auth)

    ret = {
        'wiki_id': wiki_page._primary_key if wiki_page else None,
        'wiki_name': wid,
        'wiki_content': content,
        'page': wiki_page,
        'version': version,
        'versions': _get_wiki_versions(node, wid, anonymous=anonymous),
        'is_current': is_current,
        'is_edit': False,
        'pages_current': _get_wiki_pages_current(node),
        'toc': toc,
        'category': node.category,
        'urls': {
            'api': _get_wiki_api_urls(node, wid),
            'web': _get_wiki_web_urls(node, wid),
        },
    }

    ret.update(_view_project(node, auth, primary=True))
    return ret


@must_be_valid_project
@must_be_contributor_or_public
@must_have_addon('wiki', 'node')
def wiki_page_content(wid, **kwargs):
    node = kwargs['node'] or kwargs['project']
    wiki_page = node.get_wiki_page(wid)

    return {
        'wiki_content': wiki_page.content if wiki_page else ''
    }


@must_be_valid_project  # returns project
@must_have_permission('write')  # returns user, project
@must_not_be_registration
@must_have_addon('wiki', 'node')
def project_wiki_edit(wid, auth, **kwargs):
    wid = wid.strip()
    node = kwargs['node'] or kwargs['project']
    wiki_page = node.get_wiki_page(wid)

    if wiki_page:
        version = wiki_page.version
        is_current = wiki_page.is_current
        content = wiki_page.content
        wiki_page_api_url = node.api_url_for('project_wiki_page', wid=wiki_page.page_name)
    else:
        version = 'NA'
        is_current = False
        content = ''
        wiki_page_api_url = None

    # TODO: Remove duplication with project_wiki_page
    toc = serialize_wiki_toc(node, auth=auth)
    rv = {
        'wiki_id': wiki_page._id if wiki_page else '',
        'wiki_name': wid,
        'wiki_content': content,
        'version': version,
        'versions': _get_wiki_versions(node, wid),
        'is_current': is_current,
        'is_edit': True,
        'pages_current': _get_wiki_pages_current(node),
        'toc': toc,
        'category': node.category,
        'urls': {
            'api': _get_wiki_api_urls(node, wid, {
                'content': node.api_url_for('wiki_page_content', wid=wid),
                'page': wiki_page_api_url
            }),
            'web': _get_wiki_web_urls(node, wid),
        },
    }
    rv.update(_view_project(node, auth, primary=True))
    return rv


@must_be_valid_project  # injects node or project
@must_have_permission('write')  # injects user
@must_not_be_registration
@must_have_addon('wiki', 'node')
def project_wiki_edit_post(wid, auth, **kwargs):
    wid = wid.strip()
    node = kwargs['node'] or kwargs['project']
    wiki_page = node.get_wiki_page(wid)
    redirect_url = node.web_url_for('project_wiki_page', wid=wid)

    if wiki_page:
        # Only update node wiki if content has changed
        content = wiki_page.content
        if request.form['content'] != content:
            node.update_node_wiki(wid, request.form['content'], auth)
            ret = {'status': 'success'}
        else:
            ret = {'status': 'unmodified'}
    else:
        # update_node_wiki will create a new wiki page because a page
        # with wid does not exist
        node.update_node_wiki(wid, request.form['content'], auth)
        ret = {'status': 'success'}

    return ret, http.FOUND, None, redirect_url


@must_not_be_registration
@must_have_permission('write')
@must_have_addon('wiki', 'node')
def project_wiki_rename(**kwargs):
    node = kwargs['node'] or kwargs['project']
    wid = request.json.get('pk', None)
    page = NodeWikiPage.load(wid)

    if page.page_name.lower() == 'home':
        raise HTTPError(http.BAD_REQUEST, data=dict(
            message_short='Invalid request',
            message_long='The wiki home page cannot be renamed.'
        ))

    old_name_key = to_mongo(page.page_name).lower()
    new_name_key = to_mongo(request.json.get('value', None)).strip().lower()

    if page and new_name_key:
        if new_name_key in node.wiki_pages_current:
            raise HTTPError(http.CONFLICT)

        # TODO: This should go in a Node method like node.rename_wiki
        node.wiki_pages_versions[new_name_key] = node.wiki_pages_versions[old_name_key]
        del node.wiki_pages_versions[old_name_key]
        node.wiki_pages_current[new_name_key] = node.wiki_pages_current[old_name_key]
        del node.wiki_pages_current[old_name_key]
        node.save()
        page.rename(new_name_key)
        return {'message': new_name_key}

    raise HTTPError(http.BAD_REQUEST)


@must_be_valid_project  # injects project
@must_have_permission('write')  # injects user, project
@must_not_be_registration
@must_have_addon('wiki', 'node')
def project_wiki_delete(auth, wid, **kwargs):
    node = kwargs['node'] or kwargs['project']
    page = node.get_wiki_page(wid)
    if not page:
        raise HTTPError(http.NOT_FOUND)
    node.delete_node_wiki(node, page, auth)
    node.save()
    return {}