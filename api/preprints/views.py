import re
from packaging.version import Version

from django.contrib.auth.models import AnonymousUser
from rest_framework import generics
from rest_framework.exceptions import MethodNotAllowed, NotFound, PermissionDenied, NotAuthenticated, ValidationError
from rest_framework import permissions as drf_permissions

from framework import sentry
from framework.auth.oauth_scopes import CoreScopes
from osf.models import (
    Institution,
    Preprint,
    PreprintContributor,
    ReviewAction,
)
from osf.utils.requests import check_select_for_update
from osf.utils.workflows import DefaultStates, ReviewStates

from api.actions.permissions import ReviewActionPermission
from api.actions.serializers import ReviewActionSerializer
from api.actions.views import get_review_actions_queryset
from api.base.pagination import PreprintContributorPagination
from api.base.exceptions import Conflict
from api.base.views import JSONAPIBaseView, WaterButlerMixin
from api.base.filters import ListFilterMixin, PreprintAsTargetFilterMixin, PreprintFilterMixin
from api.base.parsers import (
    JSONAPIMultipleRelationshipsParser,
    JSONAPIMultipleRelationshipsParserForRegularJSON,
    JSONAPIOnetoOneRelationshipParser,
    JSONAPIOnetoOneRelationshipParserForRegularJSON,
    JSONAPIRelationshipParser,
    JSONAPIRelationshipParserForRegularJSON,
)
from api.base.utils import absolute_reverse, get_user_auth, get_object_or_error
from api.base import permissions as base_permissions
from api.citations.utils import render_citation
from api.preprints.serializers import (
    PreprintSerializer,
    PreprintCreateSerializer,
    PreprintCreateVersionSerializer,
    PreprintCitationSerializer,
    PreprintContributorDetailSerializer,
    PreprintContributorsSerializer,
    PreprintStorageProviderSerializer,
    PreprintNodeRelationshipSerializer,
    PreprintContributorsCreateSerializer,
    PreprintsInstitutionsRelationshipSerializer,
)
from api.files.serializers import OsfStorageFileSerializer
from api.identifiers.views import IdentifierList
from api.identifiers.serializers import PreprintIdentifierSerializer
from api.institutions.serializers import InstitutionSerializer
from api.nodes.views import NodeMixin, NodeContributorsList, NodeContributorDetail, NodeFilesList, NodeStorageProvidersList, NodeStorageProvider
from api.nodes.serializers import NodeCitationStyleSerializer
from api.preprints.permissions import (
    PreprintPublishedOrAdmin,
    PreprintPublishedOrWrite,
    ModeratorIfNeverPublicWithdrawn,
    AdminOrPublic,
    ContributorDetailPermissions,
    PreprintFilesPermissions,
    PreprintInstitutionPermissionList,
)
from api.providers.workflows import Workflows, PUBLIC_STATES
from api.nodes.permissions import ContributorOrPublic
from api.base.permissions import WriteOrPublicForRelationshipInstitutions
from api.requests.permissions import PreprintRequestPermission
from api.requests.serializers import PreprintRequestSerializer, PreprintRequestCreateSerializer
from api.requests.views import PreprintRequestMixin
from api.subjects.views import BaseResourceSubjectsList, SubjectRelationshipBaseView
from api.base.metrics import PreprintMetricsViewMixin
from osf.metrics import PreprintDownload, PreprintView


class PreprintOldVersionsImmutableMixin:
    """Override method to reject modify requests for old preprint versions (except for withdrawal)"""

    @staticmethod
    def is_edit_allowed(preprint):
        if preprint.is_latest_version or preprint.machine_state == DefaultStates.INITIAL.value:
            return True
        if preprint.provider.reviews_workflow == Workflows.PRE_MODERATION.value:
            if preprint.machine_state == DefaultStates.PENDING.value or preprint.machine_state == DefaultStates.REJECTED.value:
                return True
        return False

    def handle_request(self, request, method, *args, **kwargs):
        preprint = self.get_preprint(check_object_permissions=False)
        if PreprintOldVersionsImmutableMixin.is_edit_allowed(preprint):
            return method(request, *args, **kwargs)
        message = f'User can not edit previous versions of a preprint: [_id={preprint._id}]'
        sentry.log_message(message)
        raise Conflict(detail=message)

    def update(self, request, *args, **kwargs):
        return self.handle_request(request, super().update, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return self.handle_request(request, super().create, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return self.handle_request(request, super().destroy, *args, **kwargs)


class PreprintMixin(NodeMixin):
    serializer_class = PreprintSerializer
    preprint_lookup_url_kwarg = 'preprint_id'

    def get_preprint(self, check_object_permissions=True, ignore_404=False):
        preprint_lookup_data = self.kwargs[self.preprint_lookup_url_kwarg].split('_v')

        base_guid_id = preprint_lookup_data[0]
        preprint_version = preprint_lookup_data[1] if len(preprint_lookup_data) > 1 else None
        if preprint_version:
            qs = Preprint.objects.filter(versioned_guids__guid___id=base_guid_id, versioned_guids__version=preprint_version)
            preprint = qs.select_for_update().first() if check_select_for_update(self.request) else qs.select_related('node').first()
        else:
            # when pre-moderation is on, we should look for the preprint
            # in all objects as it isn't published, not in published_objects
            qs = Preprint.objects.filter(guids___id=self.kwargs[self.preprint_lookup_url_kwarg], guids___id__isnull=False)
            preprint = qs.select_for_update().first() if check_select_for_update(self.request) else qs.select_related('node').first()
            if preprint and preprint.provider.reviews_workflow != Workflows.PRE_MODERATION.value:
                qs = Preprint.published_objects.filter(versioned_guids__guid___id=base_guid_id).order_by('-versioned_guids__version')
                preprint = qs.select_for_update().first() if check_select_for_update(self.request) else qs.select_related('node').first()

        if not preprint:
            sentry.log_message(f'Preprint not found: [guid={base_guid_id}, version={preprint_version}]')
            if ignore_404:
                return
            raise NotFound
        if preprint.deleted is not None:
            sentry.log_message(f'Preprint deleted: [guid={base_guid_id}, version={preprint_version}]')
            raise NotFound

        # May raise a permission denied
        if check_object_permissions:
            self.check_object_permissions(self.request, preprint)

        user = self.request.user
        if isinstance(user, AnonymousUser):
            user_is_reviewer = user_is_contributor = False
        else:
            user_is_reviewer = user.has_groups(preprint.provider.group_names)
            user_is_contributor = preprint.is_contributor(user)

        if (
            preprint.machine_state == DefaultStates.INITIAL.value and
            not user_is_contributor and
            user_is_reviewer
        ):
            raise NotFound

        preprint_is_public = bool(
            preprint.machine_state in PUBLIC_STATES[preprint.provider.reviews_workflow]
            or preprint.machine_state == ReviewStates.WITHDRAWN.value,
        )
        if not preprint_is_public and not user_is_contributor and not user_is_reviewer:
            raise NotFound

        return preprint

class PreprintList(PreprintMetricsViewMixin, JSONAPIBaseView, generics.ListCreateAPIView, PreprintFilterMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprints_list).
    """
    # These permissions are not checked for the list of preprints, permissions handled by the query
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ContributorOrPublic,
    )

    parser_classes = (JSONAPIMultipleRelationshipsParser, JSONAPIMultipleRelationshipsParserForRegularJSON)

    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]

    serializer_class = PreprintSerializer

    ordering = ('-created')
    ordering_fields = ('created', 'date_last_transitioned')
    view_category = 'preprints'
    view_name = 'preprint-list'
    metric_map = {
        'downloads': PreprintDownload,
        'views': PreprintView,
    }

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return PreprintCreateSerializer
        else:
            return PreprintSerializer

    def get_default_queryset(self):
        auth = get_user_auth(self.request)
        auth_user = getattr(auth, 'user', None)

        # Permissions on the list objects are handled by the query
        public_only = self.metrics_requested
        queryset = self.preprints_queryset(Preprint.objects.all(), auth_user, public_only=public_only)
        # Use get_metrics_queryset to return an queryset with annotated metrics
        # iff ?metrics query param is present
        if self.metrics_requested:
            return self.get_metrics_queryset(queryset)
        else:
            return queryset

    # overrides ListAPIView
    def get_queryset(self):
        return self.get_queryset_from_request()

    # overrides PreprintMetricsViewMixin
    def get_annotated_queryset_with_metrics(self, queryset, metric_class, metric_name, after):
        return metric_class.get_top_by_count(
            qs=queryset,
            model_field='guids___id',
            metric_field='preprint_id',
            annotation=metric_name,
            after=after,
            # Limit the bucket size
            # of the ES aggregation. Otherwise,
            # the number of buckets == the number of total preprints,
            # which is too many for ES to handle
            size=200,
        )


class PreprintVersionsList(PreprintMetricsViewMixin, JSONAPIBaseView, generics.ListCreateAPIView, PreprintFilterMixin):
    # These permissions are not checked for the list of preprints, permissions handled by the query
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ContributorOrPublic,
    )

    parser_classes = (JSONAPIMultipleRelationshipsParser, JSONAPIMultipleRelationshipsParserForRegularJSON)

    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]

    serializer_class = PreprintSerializer

    ordering = ('-created')
    ordering_fields = ('created', 'date_last_transitioned')
    view_category = 'preprints'
    view_name = 'preprint-versions'
    metric_map = {
        'downloads': PreprintDownload,
        'views': PreprintView,
    }

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return PreprintCreateVersionSerializer
        else:
            return PreprintSerializer

    def get_queryset(self):
        preprint = Preprint.load(self.kwargs.get('preprint_id'))
        if not preprint:
            sentry.log_message(f'Preprint not found: [preprint_id={self.kwargs.get('preprint_id')}]')
            raise NotFound
        version_ids = preprint.versioned_guids.first().guid.versions.values_list('object_id', flat=True)
        qs = Preprint.objects.filter(id__in=version_ids)

        auth = get_user_auth(self.request)
        auth_user = getattr(auth, 'user', None)

        # Permissions on the list objects are handled by the query
        public_only = self.metrics_requested
        qs = qs.filter(Preprint.objects.preprint_versions_permissions_query(auth_user, public_only=public_only))

        return qs

    def create(self, request, *args, **kwargs):
        request.data['type'] = 'preprints'
        request.data['create_from_guid'] = kwargs.get('preprint_id')
        return super().create(request, *args, **kwargs)


class PreprintDetail(PreprintOldVersionsImmutableMixin, PreprintMetricsViewMixin, JSONAPIBaseView, generics.RetrieveUpdateAPIView, PreprintMixin, WaterButlerMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprints_read).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ModeratorIfNeverPublicWithdrawn,
        ContributorOrPublic,
        PreprintPublishedOrWrite,
    )
    parser_classes = (
        JSONAPIMultipleRelationshipsParser,
        JSONAPIMultipleRelationshipsParserForRegularJSON,
    )

    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]

    serializer_class = PreprintSerializer

    view_category = 'preprints'
    view_name = 'preprint-detail'
    metric_map = {
        'downloads': PreprintDownload,
        'views': PreprintView,
    }

    def add_metric_to_object(self, obj, metric_class, metric_name, after):
        count = metric_class.get_count_for_preprint(obj, after=after)
        setattr(obj, metric_name, count)
        return obj

    def get_object(self):
        preprint = self.get_preprint()
        # If requested, add metrics to object
        if self.metrics_requested:
            self.add_metrics_to_object(preprint)
        return preprint

    def get_parser_context(self, http_request):
        """
        Tells parser that type is required in request
        """
        res = super().get_parser_context(http_request)
        res['legacy_type_allowed'] = True
        return res


class PreprintNodeRelationship(PreprintOldVersionsImmutableMixin, JSONAPIBaseView, generics.RetrieveUpdateAPIView, PreprintMixin):
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ContributorOrPublic,
        PreprintPublishedOrWrite,
    )

    view_category = 'preprints'
    view_name = 'preprint-node-relationship'

    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]

    serializer_class = PreprintNodeRelationshipSerializer
    parser_classes = (JSONAPIOnetoOneRelationshipParser, JSONAPIOnetoOneRelationshipParserForRegularJSON)

    def get_object(self):
        preprint = self.get_preprint()
        auth = get_user_auth(self.request)
        type_ = 'linked_preprint_nodes' if Version(self.request.version) < Version('2.13') else 'nodes'
        obj = {
            'data': {'id': preprint.node._id, 'type': type_} if preprint.node and preprint.node.can_view(auth) else None,
            'self': preprint,
        }
        return obj


class PreprintCitationDetail(PreprintOldVersionsImmutableMixin, JSONAPIBaseView, generics.RetrieveAPIView, PreprintMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprints_citation_list).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
    )

    required_read_scopes = [CoreScopes.PREPRINT_CITATIONS_READ]
    required_write_scopes = [CoreScopes.NULL]

    serializer_class = PreprintCitationSerializer
    view_category = 'preprints'
    view_name = 'preprint-citation'

    def get_object(self):
        preprint = self.get_preprint()
        auth = get_user_auth(self.request)

        if preprint.can_view(auth):
            return preprint.csl

        raise PermissionDenied if auth.user else NotAuthenticated


class PreprintCitationStyleDetail(JSONAPIBaseView, generics.RetrieveAPIView, PreprintMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprints_citation_read).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
    )

    required_read_scopes = [CoreScopes.PREPRINT_CITATIONS_READ]
    required_write_scopes = [CoreScopes.NULL]

    serializer_class = NodeCitationStyleSerializer
    view_category = 'preprint'
    view_name = 'preprint-citation'

    def get_object(self):
        preprint = self.get_preprint()
        auth = get_user_auth(self.request)
        style = self.kwargs.get('style_id')

        if preprint.can_view(auth):
            try:
                citation = render_citation(node=preprint, style=style)
            except ValueError as err:  # style requested could not be found
                csl_name = re.findall(r'[a-zA-Z]+\.csl', str(err))[0]
                raise NotFound(f'{csl_name} is not a known style.')

            return {'citation': citation, 'id': style}

        raise PermissionDenied if auth.user else NotAuthenticated


class PreprintIdentifierList(IdentifierList, PreprintMixin):
    """List of identifiers for a specified preprint. *Read-only*.

    ##Identifier Attributes

    OSF Identifier entities have the "identifiers" `type`.

        name           type                   description
        ----------------------------------------------------------------------------
        category       string                 e.g. 'ark', 'doi'
        value          string                 the identifier value itself

    ##Links

        self: this identifier's detail page

    ##Relationships

    ###Referent

    The identifier is refers to this preprint.

    ##Actions

    *None*.

    ##Query Params

     Identifiers may be filtered by their category.

    #This Request/Response

    """

    permission_classes = (
        PreprintPublishedOrAdmin,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
    )
    serializer_class = PreprintIdentifierSerializer
    required_read_scopes = [CoreScopes.IDENTIFIERS_READ]
    required_write_scopes = [CoreScopes.NULL]

    preprint_lookup_url_kwarg = 'preprint_id'

    view_category = 'preprints'
    view_name = 'identifier-list'

    # overrides IdentifierList
    def get_object(self, check_object_permissions=True):
        return self.get_preprint(check_object_permissions=check_object_permissions)


class PreprintContributorsList(PreprintOldVersionsImmutableMixin, NodeContributorsList, PreprintMixin):
    permission_classes = (
        AdminOrPublic,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        PreprintPublishedOrAdmin,
    )

    pagination_class = PreprintContributorPagination

    required_read_scopes = [CoreScopes.PREPRINT_CONTRIBUTORS_READ]
    required_write_scopes = [CoreScopes.PREPRINT_CONTRIBUTORS_WRITE]

    view_category = 'preprints'
    view_name = 'preprint-contributors'
    serializer_class = PreprintContributorsSerializer

    def get_default_queryset(self):
        preprint = self.get_preprint()
        return preprint.preprintcontributor_set.all().prefetch_related('user__guids')

    # overrides NodeContributorsList
    def get_serializer_class(self):
        """
        Use NodeContributorDetailSerializer which requires 'id'
        """
        if self.request.method == 'PUT' or self.request.method == 'PATCH' or self.request.method == 'DELETE':
            return PreprintContributorDetailSerializer
        elif self.request.method == 'POST':
            return PreprintContributorsCreateSerializer
        else:
            return PreprintContributorsSerializer

    def get_resource(self):
        return self.get_preprint(ignore_404=True)

    # Overrides NodeContributorsList
    def get_serializer_context(self):
        context = JSONAPIBaseView.get_serializer_context(self)
        context['resource'] = self.get_resource()
        context['default_email'] = 'preprint'
        return context


class PreprintContributorDetail(PreprintOldVersionsImmutableMixin, NodeContributorDetail, PreprintMixin):

    permission_classes = (
        ContributorDetailPermissions,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
    )

    view_category = 'preprints'
    view_name = 'preprint-contributor-detail'
    serializer_class = PreprintContributorDetailSerializer

    required_read_scopes = [CoreScopes.PREPRINT_CONTRIBUTORS_READ]
    required_write_scopes = [CoreScopes.PREPRINT_CONTRIBUTORS_WRITE]

    def get_resource(self):
        return self.get_preprint(ignore_404=True)

    # overrides RetrieveAPIView
    def get_object(self):
        preprint = self.get_preprint()
        user = self.get_user()
        # May raise a permission denied
        self.check_object_permissions(self.request, user)
        try:
            return preprint.preprintcontributor_set.get(user=user)
        except PreprintContributor.DoesNotExist:
            raise NotFound(f'{user} cannot be found in the list of contributors.')

    def get_serializer_context(self):
        context = JSONAPIBaseView.get_serializer_context(self)
        context['resource'] = self.get_preprint()
        context['user'] = self.get_user()
        context['default_email'] = 'preprint'
        return context

    def perform_destroy(self, instance):
        preprint = self.get_resource()
        auth = get_user_auth(self.request)
        if preprint.visible_contributors.count() == 1 and instance.visible:
            raise ValidationError('Must have at least one visible contributor')
        if preprint.machine_state == DefaultStates.INITIAL.value and preprint.creator_id == instance.user.id == auth.user.id:
            raise ValidationError(
                'You cannot delete yourself at this time. '
                'Have another admin contributor do that after you’ve submitted your preprint',
            )
        removed = preprint.remove_contributor(instance, auth)
        if not removed:
            raise ValidationError('Must have at least one registered admin contributor')


class PreprintBibliographicContributorsList(PreprintContributorsList):
    permission_classes = (
        AdminOrPublic,
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
    )

    pagination_class = PreprintContributorPagination
    serializer_class = PreprintContributorsSerializer

    view_category = 'preprints'
    view_name = 'preprint-bibliographic-contributors'

    def get_default_queryset(self):
        contributors = super().get_default_queryset()
        return contributors.filter(visible=True)

    def post(self, request, *args, **kwargs):
        raise MethodNotAllowed(method=request.method)


class PreprintSubjectsList(BaseResourceSubjectsList, PreprintMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprint_subjects_list).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ModeratorIfNeverPublicWithdrawn,
        ContributorOrPublic,
        PreprintPublishedOrWrite,
    )

    required_read_scopes = [CoreScopes.PREPRINTS_READ]

    view_category = 'preprints'
    view_name = 'preprint-subjects'

    def get_resource(self):
        return self.get_preprint()


class PreprintSubjectsRelationship(PreprintOldVersionsImmutableMixin, SubjectRelationshipBaseView, PreprintMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprint_subjects_list).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ModeratorIfNeverPublicWithdrawn,
        ContributorOrPublic,
        PreprintPublishedOrWrite,
    )

    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]

    view_category = 'preprints'
    view_name = 'preprint-relationships-subjects'

    def get_resource(self, check_object_permissions=True):
        return self.get_preprint(check_object_permissions=check_object_permissions)

    def get_object(self):
        resource = self.get_resource(check_object_permissions=False)
        obj = {
            'data': resource.subjects.all(),
            'self': resource,
        }
        self.check_object_permissions(self.request, resource)
        return obj


class PreprintActionList(JSONAPIBaseView, generics.ListCreateAPIView, PreprintAsTargetFilterMixin, PreprintMixin):
    """Action List *Read-only*

    Actions represent state changes and/or comments on a reviewable object (e.g. a preprint)

    ##Action Attributes

        name                            type                                description
        ====================================================================================
        date_created                    iso8601 timestamp                   timestamp that the action was created
        date_modified                   iso8601 timestamp                   timestamp that the action was last modified
        from_state                      string                              state of the reviewable before this action was created
        to_state                        string                              state of the reviewable after this action was created
        comment                         string                              comment explaining the state change
        trigger                         string                              name of the trigger for this action

    ##Relationships

    ###Target
    Link to the object (e.g. preprint) this action acts on

    ###Provider
    Link to detail for the target object's provider

    ###Creator
    Link to the user that created this action

    ##Links
    - `self` -- Detail page for the current action

    ##Query Params

    + `page=<Int>` -- page number of results to view, default 1

    + `filter[<fieldname>]=<Str>` -- fields and values to filter the search results on.

    Actions may be filtered by their `id`, `from_state`, `to_state`, `date_created`, `date_modified`, `creator`, `provider`, `target`
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        ReviewActionPermission,
    )

    required_read_scopes = [CoreScopes.ACTIONS_READ]
    required_write_scopes = [CoreScopes.ACTIONS_WRITE]

    parser_classes = (JSONAPIMultipleRelationshipsParser, JSONAPIMultipleRelationshipsParserForRegularJSON)
    serializer_class = ReviewActionSerializer
    model_class = ReviewAction

    ordering = ('-created',)
    view_category = 'preprints'
    view_name = 'preprint-review-action-list'

    # overrides ListCreateAPIView
    def perform_create(self, serializer):
        target = serializer.validated_data['target']
        self.check_object_permissions(self.request, target)

        if not target.provider.is_reviewed:
            url = absolute_reverse(
                'providers:preprint-providers:preprint-provider-detail',
                kwargs={
                    'provider_id': target.provider._id,
                    'version': self.request.parser_context['kwargs']['version'],
                },
            )
            raise Conflict(
                f'{target.provider.name} is an unmoderated provider. '
                f'If you are an admin, set up moderation by setting `reviews_workflow` at {url}',
            )

        serializer.save(user=self.request.user)

    # overrides ListFilterMixin
    def get_default_queryset(self):
        return get_review_actions_queryset().filter(target_id=self.get_preprint().id)

    # overrides ListAPIView
    def get_queryset(self):
        return self.get_queryset_from_request()


class PreprintStorageProvidersList(NodeStorageProvidersList, PreprintMixin):
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        ContributorOrPublic,
        base_permissions.TokenHasScope,
        PreprintFilesPermissions,
    )

    required_read_scopes = [CoreScopes.PREPRINT_FILE_READ]
    required_write_scopes = [CoreScopes.PREPRINT_FILE_WRITE]

    serializer_class = PreprintStorageProviderSerializer
    view_category = 'preprints'
    view_name = 'preprint-storage-providers'

    def get_provider_item(self, provider_name):
        return NodeStorageProvider(self.get_preprint(), provider_name)

    def get_queryset(self):
        # Preprints Providers restricted so only osfstorage is allowed
        return [
            self.get_provider_item('osfstorage'),
        ]


class PreprintFilesList(NodeFilesList, PreprintMixin):
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        PreprintFilesPermissions,
    )
    required_read_scopes = [CoreScopes.PREPRINT_FILE_READ]
    required_write_scopes = [CoreScopes.PREPRINT_FILE_WRITE]

    view_category = 'preprints'
    view_name = 'preprint-files'

    serializer_class = OsfStorageFileSerializer

    def get_queryset(self):
        self.kwargs[self.path_lookup_url_kwarg] = '/'
        self.kwargs[self.provider_lookup_url_kwarg] = 'osfstorage'
        return super().get_queryset()

    def get_resource(self):
        return get_object_or_error(Preprint, self.kwargs['preprint_id'], self.request)

class PreprintRequestListCreate(JSONAPIBaseView, generics.ListCreateAPIView, ListFilterMixin, PreprintRequestMixin):
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        PreprintRequestPermission,
    )

    required_read_scopes = [CoreScopes.PREPRINT_REQUESTS_READ]
    required_write_scopes = [CoreScopes.PREPRINT_REQUESTS_WRITE]

    parser_classes = (JSONAPIMultipleRelationshipsParser, JSONAPIMultipleRelationshipsParserForRegularJSON)

    serializer_class = PreprintRequestSerializer

    view_category = 'preprint-requests'
    view_name = 'preprint-request-list'

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return PreprintRequestCreateSerializer
        else:
            return PreprintRequestSerializer

    def get_default_queryset(self):
        return self.get_target().requests.all()

    def get_queryset(self):
        return self.get_queryset_from_request()


class PreprintInstitutionsList(JSONAPIBaseView, generics.ListAPIView, ListFilterMixin, PreprintMixin):
    """The documentation for this endpoint can be found [here](https://developer.osf.io/#operation/preprint_institutions_list).
    """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        PreprintInstitutionPermissionList,
    )

    required_read_scopes = [CoreScopes.PREPRINTS_READ, CoreScopes.INSTITUTION_READ]
    required_write_scopes = [CoreScopes.NULL]
    serializer_class = InstitutionSerializer

    model = Institution
    view_category = 'preprints'
    view_name = 'preprints-institutions'

    ordering = ('-id',)

    def get_resource(self):
        return self.get_preprint()

    def get_queryset(self):
        return self.get_resource().affiliated_institutions.all()


class PreprintInstitutionsRelationship(PreprintOldVersionsImmutableMixin, JSONAPIBaseView, generics.RetrieveUpdateAPIView, PreprintMixin):
    """ """
    permission_classes = (
        drf_permissions.IsAuthenticatedOrReadOnly,
        base_permissions.TokenHasScope,
        WriteOrPublicForRelationshipInstitutions,
    )
    required_read_scopes = [CoreScopes.PREPRINTS_READ]
    required_write_scopes = [CoreScopes.PREPRINTS_WRITE]
    serializer_class = PreprintsInstitutionsRelationshipSerializer
    parser_classes = (JSONAPIRelationshipParser, JSONAPIRelationshipParserForRegularJSON)

    view_category = 'preprints'
    view_name = 'preprint-relationships-institutions'

    def get_resource(self):
        return self.get_preprint(check_object_permissions=False)

    def get_object(self):
        preprint = self.get_resource()
        obj = {
            'data': preprint.affiliated_institutions.all(),
            'self': preprint,
        }
        self.check_object_permissions(self.request, obj)
        return obj

    def patch(self, *args, **kwargs):
        raise MethodNotAllowed(self.request.method)
