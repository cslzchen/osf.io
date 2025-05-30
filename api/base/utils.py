from furl import furl
from urllib.parse import urlunsplit, urlsplit, parse_qs, urlencode
from packaging.version import Version
from hashids import Hashids
import waffle

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import QuerySet
from rest_framework import fields
from rest_framework import exceptions
from rest_framework.exceptions import NotFound
from rest_framework.reverse import reverse

from api.base.exceptions import Gone, UserGone
from api.base.settings import HASHIDS_SALT
from framework.auth import Auth
from framework.auth.cas import CasResponse
from framework.auth.oauth_scopes import ComposedScopes, normalize_scopes
from osf.models.base import GuidMixin, VersionedGuidMixin
from osf.utils.requests import check_select_for_update
from website import settings as website_settings
from website import util as website_util  # noqa

# See https://github.com/encode/django-rest-framework/blob/3.13.1/rest_framework/fields.py#L699-L721
TRUTHY = fields.BooleanField.TRUE_VALUES
FALSY = fields.BooleanField.FALSE_VALUES

UPDATE_METHODS = ['PUT', 'PATCH']

hashids = Hashids(alphabet='abcdefghijklmnopqrstuvwxyz', salt=HASHIDS_SALT)


def decompose_field(field):
    """
    Returns the lowest nested field. If no nesting, returns the original field.
    :param field, highest field

    Assumes nested structures like the following:
    - field, field.field, field.child_relation, field.field.child_relation, etc.
    """
    while hasattr(field, 'field'):
        field = field.field
    return getattr(field, 'child_relation', field)


def is_bulk_request(request):
    """
    Returns True if bulk request.  Can be called as early as the parser.
    """
    content_type = request.content_type
    return 'ext=bulk' in content_type


def is_truthy(value):
    if isinstance(value, bool) or value is None:
        return value
    return str(value).lower() in TRUTHY


def is_falsy(value):
    if isinstance(value, bool) or value is None:
        return not value
    return str(value).lower() in FALSY


def get_user_auth(request):
    """Given a Django request object, return an ``Auth`` object with the
    authenticated user attached to it.
    """
    user = request.user
    private_key = None
    if hasattr(request, 'query_params'):  # allows django WSGIRequest to be used as well
        private_key = request.query_params.get('view_only', None)
    if user.is_anonymous:
        auth = Auth(None, private_key=private_key)
    else:
        auth = Auth(user, private_key=private_key)
    return auth


def absolute_reverse(view_name, query_kwargs=None, args=None, kwargs=None):
    """Like django's `reverse`, except returns an absolute URL. Also add query parameters."""
    relative_url = reverse(view_name, kwargs=kwargs)

    url = website_util.api_v2_url(relative_url, params=query_kwargs, base_prefix='')
    return url


def get_object_or_error(model_or_qs, query_or_pk=None, request=None, display_name=None, check_deleted=True):
    OSFUser = apps.get_model('osf', 'OSFUser')
    if not request:
        # for backwards compat with existing get_object_or_error usages
        raise TypeError('request is a required argument')

    obj = query = None
    model_cls = model_or_qs
    select_for_update = check_select_for_update(request)

    if isinstance(model_or_qs, QuerySet):
        # they passed a queryset
        model_cls = model_or_qs.model
        try:
            obj = model_or_qs.select_for_update().get() if select_for_update else model_or_qs.get()
        except model_cls.DoesNotExist:
            raise NotFound

    elif isinstance(query_or_pk, str):
        # If the class is a subclass of `VersionedGuidMixin`, get obj directly from model using `.load()`. The naming
        # for `query_or_pk` no longer matches the actual case. It is neither a query nor a pk, but a guid str.
        if issubclass(model_cls, VersionedGuidMixin):
            obj = model_cls.load(query_or_pk, select_for_update=select_for_update)
        # If the class is a subclass of `GuidMixin` (except for `VersionedGuidMixin`), turn it into a query dictionary.
        # The naming for `query_or_pk` no longer matches the actual case either. It is neither a query nor a pk, but a
        # 5-char guid str. We should be able to use the `.load()` the same way as in the `VersionedGuidMixin` case.
        elif issubclass(model_cls, GuidMixin):
            # if it's a subclass of GuidMixin we know it's primary_identifier_name
            query = {'guids___id': query_or_pk}
        else:
            if hasattr(model_cls, 'primary_identifier_name'):
                # primary_identifier_name gives us the natural key for the model
                query = {model_cls.primary_identifier_name: query_or_pk}
            else:
                # fall back to modmcompatiblity's load method since we don't know their PIN
                obj = model_cls.load(query_or_pk, select_for_update=select_for_update)
    else:
        # they passed a query
        try:
            obj = model_cls.objects.filter(query_or_pk).select_for_update().get() if select_for_update else model_cls.objects.get(query_or_pk)
        except model_cls.DoesNotExist:
            raise NotFound

    if not obj:
        if not query:
            # if we don't have a query or an object throw 404
            raise NotFound
        try:
            # TODO This could be added onto with eager on the queryset and the embedded fields of the api
            if isinstance(query, dict):
                obj = model_cls.objects.get(**query) if not select_for_update else model_cls.objects.filter(**query).select_for_update().get()
            else:
                obj = model_cls.objects.get(query) if not select_for_update else model_cls.objects.filter(query).select_for_update().get()
        except ObjectDoesNotExist:
            raise NotFound

    # For objects that have been disabled (is_active is False), return a 410.
    # The User model is an exception because we still want to allow
    # users who are unconfirmed or unregistered, but not users who have been
    # disabled.
    if model_cls is OSFUser and obj.is_disabled:
        raise UserGone(user=obj)
    if check_deleted and (model_cls is not OSFUser and not getattr(obj, 'is_active', True) or getattr(obj, 'is_deleted', False) or getattr(obj, 'deleted', False)):
        if display_name is None:
            raise Gone
        else:
            raise Gone(detail=f'The requested {display_name} is no longer available.')
    return obj


def default_node_list_queryset(model_cls):
    Node = apps.get_model('osf', 'Node')
    Registration = apps.get_model('osf', 'Registration')
    assert model_cls in {Node, Registration}
    return model_cls.objects.filter(is_deleted=False)


def default_node_permission_queryset(user, model_cls):
    """
    Return nodes that are either public or you have perms because you're a contributor.
    Implicit admin permissions not included here (NodeList, UserNodes, for example, don't factor this in.)
    """
    Node = apps.get_model('osf', 'Node')
    Registration = apps.get_model('osf', 'Registration')
    assert model_cls in {Node, Registration}
    return model_cls.objects.get_nodes_for_user(user, include_public=True)


def default_node_list_permission_queryset(user, model_cls, **annotations):
    # **DO NOT** change the order of the querysets below.
    # If get_roots() is called on default_node_list_qs & default_node_permission_qs,
    # Django's alaising will break and the resulting QS will be empty and you will be sad.
    qs = default_node_permission_queryset(user, model_cls) & default_node_list_queryset(model_cls)
    if annotations:
        qs = qs.annotate(**annotations)
    return qs.filter(deleted=None)


def extend_querystring_params(url, params):
    scheme, netloc, path, query, _ = urlsplit(url)
    orig_params = parse_qs(query)
    orig_params.update(params)
    query = urlencode(orig_params, True)
    return urlunsplit([scheme, netloc, path, query, ''])


def extend_querystring_if_key_exists(url, request, key):
    if key in request.query_params.keys():
        return extend_querystring_params(url, {key: request.query_params.get(key)})
    return url


def has_admin_scope(request):
    """ Helper function to determine if a request should be treated
        as though it has the `osf.admin` scope. This includes both
        tokened requests that do, and requests that are made via the
        OSF (i.e. have an osf cookie)
    """
    cookie = request.COOKIES.get(website_settings.COOKIE_NAME)
    if cookie:
        return bool(request.session and request.session.get('auth_user_id', None))

    token = request.auth
    if token is None or not isinstance(token, CasResponse):
        return False

    return set(ComposedScopes.ADMIN_LEVEL).issubset(normalize_scopes(token.attributes['accessTokenScope']))


def has_pigeon_scope(request):
    """ Helper function to determine if a request token has OSF pigeon scope
    """
    token = request.auth
    if token is None or not isinstance(token, CasResponse):
        return False

    if token.attributes['accessToken'] == website_settings.PIGEON_CALLBACK_BEARER_TOKEN:
        return True
    else:
        return False


def is_deprecated(request_version, min_version=None, max_version=None):
    if not min_version and not max_version:
        raise NotImplementedError('Must specify min or max version.')
    min_version_deprecated = min_version and Version(request_version) < Version(str(min_version))
    max_version_deprecated = max_version and Version(request_version) > Version(str(max_version))
    if min_version_deprecated or max_version_deprecated:
        return True
    return False


def waterbutler_api_url_for(node_id, provider, path='/', _internal=False, base_url=None, **kwargs):
    assert path.startswith('/'), 'Path must always start with /'
    if provider != 'osfstorage':
        base_url = None
    # NOTE: furl encoding to be verified later
    url = furl(website_settings.WATERBUTLER_INTERNAL_URL if _internal else (base_url or website_settings.WATERBUTLER_URL))
    segments = ['v1', 'resources', node_id, 'providers', provider] + path.split('/')[1:]
    url.add(path=segments)
    url.args.update(kwargs)
    return url.url

def assert_resource_type(obj, resource_tuple):
    assert type(resource_tuple) is tuple, 'resources must be passed in as a tuple.'
    if len(resource_tuple) == 1:
        error_message = resource_tuple[0].__name__
    elif len(resource_tuple) == 2:
        error_message = resource_tuple[0].__name__ + ' or ' + resource_tuple[1].__name__
    else:
        error_message = ''
        for resource in resource_tuple[:-1]:
            error_message += resource.__name__ + ', '
        error_message += 'or ' + resource_tuple[-1].__name__

    a_or_an = 'an' if error_message[0].lower() in 'aeiou' else 'a'
    assert isinstance(obj, resource_tuple), f'obj must be {a_or_an} {error_message}; got {obj}'


class MockQueryset(list):
    """
    This class is meant to convert a simple list into a filterable queryset look-a-like.
    """

    def __init__(self, items, search, default_attrs=None, **kwargs):
        self.search = search

        for item in items:
            if default_attrs:
                item.update(default_attrs)
            self.add_dict_as_item(item)

    def __len__(self):
        return self.search.count()

    def add_dict_as_item(self, dict):
        item = type('item', (object,), dict)
        self.append(item)


def toggle_view_by_flag(flag_name, old_view, new_view):
    '''toggle between view implementations based on a feature flag

    returns a wrapper view function that:
    - when the given flag is inactive, passes thru to `old_view`
    - when the given flag is active, passes thru to `new_view`
    '''
    def _view_by_flag(request, *args, **kwargs):
        if waffle.flag_is_active(request, flag_name):
            return new_view(request, *args, **kwargs)
        return old_view(request, *args, **kwargs)
    if hasattr(new_view, 'view_class'):
        # set view_class to masquerade as a class-based view, for sake of assumptions
        # in `api_tests.base.test_views` and `api.base.serializers.RelationshipField`
        _view_by_flag.view_class = new_view.view_class  # type: ignore[attr-defined]
    return _view_by_flag


def update_contributors_permissions_and_bibliographic_status(serializer, instance, validated_data):
    '''
    Helper function for serializers to update permissions of contributors and their bibliographic status
    '''
    index = None
    if '_order' in validated_data:
        index = validated_data.pop('_order')

    auth = Auth(serializer.context['request'].user)
    node = serializer.context['resource']

    if 'bibliographic' in validated_data:
        bibliographic = validated_data.get('bibliographic')
    else:
        bibliographic = node.get_visible(instance.user)
    permission = validated_data.get('permission') or instance.permission
    try:
        if index is not None:
            node.move_contributor(instance.user, auth, index, save=True)
        node.update_contributor(instance.user, permission, bibliographic, auth, save=True)
    except node.state_error as e:
        raise exceptions.ValidationError(detail=str(e))
    except ValueError as e:
        raise exceptions.ValidationError(detail=str(e))
    instance.refresh_from_db()
    return instance
