from django.urls import re_path

from api.registrations import views
from website import settings

app_name = 'osf'

urlpatterns = [
    # Examples:
    # re_path(r'^$', 'api.views.home', name='home'),
    # re_path(r'^blog/', include('blog.urls')),
    re_path(r'^$', views.RegistrationList.as_view(), name=views.RegistrationList.view_name),
    re_path(r'^(?P<node_id>\w+)/$', views.RegistrationDetail.as_view(), name=views.RegistrationDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/bibliographic_contributors/$', views.RegistrationBibliographicContributorsList.as_view(), name=views.RegistrationBibliographicContributorsList.view_name),
    re_path(r'^(?P<node_id>\w+)/cedar_metadata_records/$', views.RegistrationCedarMetadataRecordsList.as_view(), name=views.RegistrationCedarMetadataRecordsList.view_name),
    re_path(r'^(?P<node_id>\w+)/callbacks/$', views.RegistrationCallbackView.as_view(), name=views.RegistrationCallbackView.view_name),
    re_path(r'^(?P<node_id>\w+)/children/$', views.RegistrationChildrenList.as_view(), name=views.RegistrationChildrenList.view_name),
    re_path(r'^(?P<node_id>\w+)/comments/$', views.RegistrationCommentsList.as_view(), name=views.RegistrationCommentsList.view_name),
    re_path(r'^(?P<node_id>\w+)/contributors/$', views.RegistrationContributorsList.as_view(), name=views.RegistrationContributorsList.view_name),
    re_path(r'^(?P<node_id>\w+)/contributors/(?P<user_id>\w+)/$', views.RegistrationContributorDetail.as_view(), name=views.RegistrationContributorDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/implicit_contributors/$', views.RegistrationImplicitContributorsList.as_view(), name=views.RegistrationImplicitContributorsList.view_name),
    re_path(r'^(?P<node_id>\w+)/files/$', views.RegistrationStorageProvidersList.as_view(), name=views.RegistrationStorageProvidersList.view_name),
    re_path(r'^(?P<node_id>\w+)/files/(?P<provider>\w+)(?P<path>/(?:.*/)?)$', views.RegistrationFilesList.as_view(), name=views.RegistrationFilesList.view_name),
    re_path(r'^(?P<node_id>\w+)/files/(?P<provider>\w+)(?P<path>/.+[^/])$', views.RegistrationFileDetail.as_view(), name=views.RegistrationFileDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/citation/$', views.RegistrationCitationDetail.as_view(), name=views.RegistrationCitationDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/citation/(?P<style_id>[-\w]+)/$', views.RegistrationCitationStyleDetail.as_view(), name=views.RegistrationCitationStyleDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/forks/$', views.RegistrationForksList.as_view(), name=views.RegistrationForksList.view_name),
    re_path(r'^(?P<node_id>\w+)/identifiers/$', views.RegistrationIdentifierList.as_view(), name=views.RegistrationIdentifierList.view_name),
    re_path(r'^(?P<node_id>\w+)/institutions/$', views.RegistrationInstitutionsList.as_view(), name=views.RegistrationInstitutionsList.view_name),
    re_path(r'^(?P<node_id>\w+)/relationships/institutions/$', views.RegistrationInstitutionsRelationship.as_view(), name=views.RegistrationInstitutionsRelationship.view_name),
    re_path(r'^(?P<node_id>\w+)/relationships/subjects/$', views.RegistrationSubjectsRelationship.as_view(), name=views.RegistrationSubjectsRelationship.view_name),
    re_path(r'^(?P<node_id>\w+)/linked_nodes/$', views.RegistrationLinkedNodesList.as_view(), name=views.RegistrationLinkedNodesList.view_name),
    re_path(r'^(?P<node_id>\w+)/linked_registrations/$', views.RegistrationLinkedRegistrationsList.as_view(), name=views.RegistrationLinkedRegistrationsList.view_name),
    re_path(r'^(?P<node_id>\w+)/linked_by_nodes/$', views.RegistrationLinkedByNodesList.as_view(), name=views.RegistrationLinkedByNodesList.view_name),
    re_path(r'^(?P<node_id>\w+)/linked_by_registrations/$', views.RegistrationLinkedByRegistrationsList.as_view(), name=views.RegistrationLinkedByRegistrationsList.view_name),
    re_path(r'^(?P<node_id>\w+)/logs/$', views.RegistrationLogList.as_view(), name=views.RegistrationLogList.view_name),
    re_path(r'^(?P<node_id>\w+)/node_links/$', views.RegistrationNodeLinksList.as_view(), name=views.RegistrationNodeLinksList.view_name),
    re_path(r'^(?P<node_id>\w+)/node_links/(?P<node_link_id>\w+)/', views.RegistrationNodeLinksDetail.as_view(), name=views.RegistrationNodeLinksDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/relationships/linked_nodes/$', views.RegistrationLinkedNodesRelationship.as_view(), name=views.RegistrationLinkedNodesRelationship.view_name),
    re_path(r'^(?P<node_id>\w+)/relationships/linked_registrations/$', views.RegistrationLinkedRegistrationsRelationship.as_view(), name=views.RegistrationLinkedRegistrationsRelationship.view_name),
    re_path(r'^(?P<node_id>\w+)/subjects/$', views.RegistrationSubjectsList.as_view(), name=views.RegistrationSubjectsList.view_name),
    re_path(r'^(?P<node_id>\w+)/view_only_links/$', views.RegistrationViewOnlyLinksList.as_view(), name=views.RegistrationViewOnlyLinksList.view_name),
    re_path(r'^(?P<node_id>\w+)/view_only_links/(?P<link_id>\w+)/$', views.RegistrationViewOnlyLinkDetail.as_view(), name=views.RegistrationViewOnlyLinkDetail.view_name),
    re_path(r'^(?P<node_id>\w+)/wikis/$', views.RegistrationWikiList.as_view(), name=views.RegistrationWikiList.view_name),
    re_path(r'^(?P<node_id>\w+)/actions/$', views.RegistrationActionList.as_view(), name=views.RegistrationActionList.view_name),
    re_path(r'^(?P<node_id>\w+)/requests/$', views.RegistrationRequestList.as_view(), name=views.RegistrationRequestList.view_name),
    re_path(r'^(?P<node_id>\w+)/schema_responses/$', views.RegistrationSchemaResponseList.as_view(), name=views.RegistrationSchemaResponseList.view_name),
    re_path(r'^(?P<node_id>\w+)/resources/$', views.RegistrationResourceList.as_view(), name=views.RegistrationResourceList.view_name),
]

# Routes only active in local/staging environments
if settings.DEV_MODE:
    urlpatterns.extend([
        re_path(r'^(?P<node_id>\w+)/registrations/$', views.RegistrationRegistrationsList.as_view(), name=views.RegistrationRegistrationsList.view_name),
    ])
