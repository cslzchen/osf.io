import httplib as http
from re import search, split

from framework import request
from framework.exceptions import HTTPError

from website.project.decorators import must_be_contributor
from website.project.decorators import must_have_addon


@must_be_contributor
@must_have_addon('figshare', 'node')
def figshare_set_config(*args, **kwargs):

    auth = kwargs['auth']
    node_settings = kwargs['node_addon']
    node = node_settings.owner

    # If authorized, only owner can change settings
    if not node_settings.user_settings or node_settings.user_settings.owner != auth.user:
        raise HTTPError(http.BAD_REQUEST)

    figshare_title = request.json.get('figshare_title', '')
    figshare_url = request.json.get('figshare_value', '')

    if search('project', figshare_url):
        figshare_type = 'project'
        figshare_id = split(r'[\_/]', figshare_url)[-1]
    else:
        figshare_type = 'article'
        figshare_id = split(r'[\_/]', figshare_url)[-1]

    #Limit to projects only
    if not figshare_id or not figshare_title or figshare_type != 'project':
        raise HTTPError(http.BAD_REQUEST)

    changed = (
        figshare_id != node_settings.figshare_id or
        figshare_type != node_settings.figshare_type or
        figshare_title != node_settings.figshare_title
    )

    if changed:
        node_settings.figshare_id = figshare_id
        node_settings.figshare_type = figshare_type
        node_settings.figshare_title = figshare_title
        node_settings.save()

        node.add_log(
            action='figshare_content_linked',
            params={
                'project': node.parent_id,
                'node': node._id,
                'figshare': {
                    'type': figshare_type,
                    'id': figshare_id,
                    'title': figshare_title,
                }
            },
            auth=auth,
        )
    return {}


@must_be_contributor
@must_have_addon('figshare', 'node')
def figshare_unlink(*args, **kwargs):
    auth = kwargs['auth']
    node = kwargs['node'] or kwargs['project']
    figshare_node = kwargs['node_addon']

    # If authorized, only owner can change settings
    if not figshare_node.user_settings or figshare_node.user_settings.owner != auth.user:
        raise HTTPError(http.BAD_REQUEST)
    node.add_log(
        action='figshare_content_unlinked',
        params={
            'project': node.parent_id,
            'node': node._id,
            'figshare': {
                'type': figshare_node.figshare_type,
                'id': figshare_node.figshare_id
            }
        },
        auth=auth,
    )
    figshare_node.figshare_id = None
    figshare_node.figshare_type = None
    figshare_node.save()
