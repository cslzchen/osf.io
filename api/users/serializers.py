from typing import Any, Dict

from django.utils import timezone
from jsonschema import validate, Draft7Validator, ValidationError as JsonSchemaValidationError
from rest_framework import exceptions
from rest_framework import serializers as ser

from addons.twofactor.models import UserSettings as TwoFactorUserSettings
from api.base.exceptions import InvalidModelValueError, Conflict
from api.base.schemas.utils import validate_user_json, from_json
from api.base.serializers import (
    BaseAPISerializer,
    JSONAPISerializer,
    JSONAPIRelationshipSerializer,
    VersionedDateTimeField,
    HideIfDisabled,
    IDField,
    LinksField,
    TypeField,
    RelationshipField,
    JSONAPIListField,
    ShowIfCurrentUser,
)
from api.base.utils import (
    absolute_reverse,
    default_node_list_queryset,
    get_user_auth,
    is_deprecated,
    hashids,
    get_object_or_error,
)

from api.base.versioning import get_kebab_snake_case_field
from api.nodes.serializers import NodeSerializer, RegionRelationshipField
from framework.auth.views import send_confirm_email_async
from osf.exceptions import ValidationValueError, ValidationError, BlockedEmailError
from osf.models import Email, Node, OSFUser, Preprint, Registration, UserMessage, Institution
from osf.models.user_message import MessageTypes
from osf.models.provider import AbstractProviderGroupObjectPermission
from osf.utils.requests import string_type_request_headers
from website.profile.views import update_osf_help_mails_subscription, update_mailchimp_subscription
from website.settings import MAILCHIMP_GENERAL_LIST, OSF_HELP_LIST, CONFIRM_REGISTRATIONS_BY_EMAIL
from website.util import api_v2_url


class SocialField(ser.DictField):
    def __init__(self, min_version, **kwargs):
        super().__init__(**kwargs)
        self.min_version = min_version
        self.help_text = f'This field will change data formats after version {self.min_version}'

    def to_representation(self, value):
        old_social_string_fields = ['twitter', 'github', 'linkedIn']
        request = self.context.get('request')
        show_old_format = request and is_deprecated(request.version, self.min_version) and request.method == 'GET'
        if show_old_format:
            social = value.copy()
            for key in old_social_string_fields:
                if social.get(key):
                    social[key] = value[key][0]
                elif social.get(key) == []:
                    social[key] = ''
            value = social
        return super().to_representation(value)


class UserSerializer(JSONAPISerializer):
    filterable_fields = frozenset([
        'full_name',
        'given_name',
        'middle_names',
        'family_name',
        'id',
    ])
    writeable_method_fields = frozenset([
        'accepted_terms_of_service',
    ])

    non_anonymized_fields = [
        'type',
    ]

    id = IDField(source='_id', read_only=True)
    type = TypeField()
    full_name = ser.CharField(source='fullname', required=True, label='Full name', help_text='Display name used in the general user interface', max_length=186)
    given_name = ser.CharField(required=False, allow_blank=True, help_text='For bibliographic citations')
    middle_names = ser.CharField(required=False, allow_blank=True, help_text='For bibliographic citations')
    family_name = ser.CharField(required=False, allow_blank=True, help_text='For bibliographic citations')
    suffix = HideIfDisabled(ser.CharField(required=False, allow_blank=True, help_text='For bibliographic citations'))
    date_registered = HideIfDisabled(VersionedDateTimeField(read_only=True))
    active = HideIfDisabled(ser.BooleanField(read_only=True, source='is_active'))
    timezone = HideIfDisabled(ser.CharField(required=False, help_text="User's timezone, e.g. 'Etc/UTC"))
    locale = HideIfDisabled(ser.CharField(required=False, help_text="User's locale, e.g.  'en_US'"))
    social = SocialField(required=False, min_version='2.10')
    employment = JSONAPIListField(required=False, source='jobs')
    education = JSONAPIListField(required=False, source='schools')
    allow_indexing = ShowIfCurrentUser(ser.BooleanField(required=False, allow_null=True))
    can_view_reviews = ShowIfCurrentUser(ser.SerializerMethodField(help_text='Whether the current user has the `view_submissions` permission to ANY reviews provider.'))
    accepted_terms_of_service = ShowIfCurrentUser(ser.SerializerMethodField())

    links = HideIfDisabled(
        LinksField(
            {
                'html': 'absolute_url',
                'profile_image': 'profile_image_url',
            },
        ),
    )

    nodes = HideIfDisabled(
        RelationshipField(
            related_view='users:user-nodes',
            related_view_kwargs={'user_id': '<_id>'},
            related_meta={
                'projects_in_common': 'get_projects_in_common',
                'count': 'get_node_count',
            },
        ),
    )

    groups = HideIfDisabled(
        RelationshipField(
            related_view='users:user-groups',
            related_view_kwargs={'user_id': '<_id>'},
        ),
    )

    registrations = HideIfDisabled(
        RelationshipField(
            related_view='users:user-registrations',
            related_view_kwargs={'user_id': '<_id>'},
            related_meta={'count': 'get_registration_count'},
        ),
    )

    institutions = HideIfDisabled(
        RelationshipField(
            related_view='users:user-institutions',
            related_view_kwargs={'user_id': '<_id>'},
            self_view='users:user-institutions-relationship',
            self_view_kwargs={'user_id': '<_id>'},
            related_meta={'count': 'get_institutions_count'},
        ),
    )

    preprints = HideIfDisabled(
        RelationshipField(
            related_view='users:user-preprints',
            related_view_kwargs={'user_id': '<_id>'},
            related_meta={'count': 'get_preprint_count'},
        ),
    )

    draft_preprints = HideIfDisabled(
        RelationshipField(
            related_view='users:user-draft-preprints',
            related_view_kwargs={'user_id': '<_id>'},
            related_meta={'count': 'get_draft_preprint_count'},
        ),
    )

    emails = ShowIfCurrentUser(
        RelationshipField(
            related_view='users:user-emails',
            related_view_kwargs={'user_id': '<_id>'},
        ),
    )

    default_region = ShowIfCurrentUser(
        RegionRelationshipField(
            related_view='regions:region-detail',
            related_view_kwargs={'region_id': 'get_default_region_id'},
            read_only=False,
        ),
    )

    settings = ShowIfCurrentUser(
        RelationshipField(
            related_view='users:user_settings',
            related_view_kwargs={'user_id': '<_id>'},
            read_only=True,
        ),
    )

    class Meta:
        type_ = 'users'

    def get_projects_in_common(self, obj):
        user = get_user_auth(self.context['request']).user
        if obj == user:
            return user.contributor_or_group_member_to.count()
        return obj.n_projects_in_common(user)

    def absolute_url(self, obj):
        if obj is not None:
            return obj.absolute_url
        return None

    def get_absolute_url(self, obj):
        return absolute_reverse(
            'users:user-detail', kwargs={
                'user_id': obj._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def get_node_count(self, obj):
        default_queryset = obj.nodes_contributor_or_group_member_to
        auth = get_user_auth(self.context['request'])
        if obj != auth.user:
            return Node.objects.get_nodes_for_user(auth.user, base_queryset=default_queryset, include_public=True).count()
        return default_queryset.count()

    def get_registration_count(self, obj):
        auth = get_user_auth(self.context['request'])
        user_registration = default_node_list_queryset(model_cls=Registration).filter(contributor__user__id=obj.id)
        return user_registration.can_view(user=auth.user, private_link=auth.private_link).count()

    def get_preprint_count(self, obj):
        auth_user = get_user_auth(self.context['request']).user
        user_preprints_query = Preprint.objects.filter(_contributors__guids___id=obj._id).exclude(machine_state='initial')
        return Preprint.objects.can_view(user_preprints_query, auth_user, allow_contribs=False).count()

    def get_draft_preprint_count(self, obj):
        auth_user = get_user_auth(self.context['request']).user
        user_preprints_query = Preprint.objects.filter(_contributors__guids___id=obj._id).filter(machine_state='initial')
        return Preprint.objects.can_view(user_preprints_query, auth_user, allow_contribs=False).count()

    def get_institutions_count(self, obj):
        if isinstance(obj, OSFUser):
            return obj.get_affiliated_institutions().count()
        return obj.affiliated_institutions.count()

    def get_can_view_reviews(self, obj):
        group_qs = AbstractProviderGroupObjectPermission.objects.filter(group__user=obj, permission__codename='view_submissions')
        return group_qs.exists() or obj.abstractprovideruserobjectpermission_set.filter(permission__codename='view_submissions') or []

    def get_default_region_id(self, obj):
        try:
            # use the annotated value if possible
            region_id = obj.default_region
        except AttributeError:
            # use computed property if region annotation does not exist
            region_id = obj.osfstorage_region._id
        return region_id

    def get_accepted_terms_of_service(self, obj):
        return bool(obj.accepted_terms_of_service)

    def profile_image_url(self, user):
        size = self.context['request'].query_params.get('profile_image_size')
        return user.profile_image_url(size=size)

    def validate_employment(self, value):
        validate_user_json(value, 'employment-schema.json')
        return value

    def validate_education(self, value):
        validate_user_json(value, 'education-schema.json')
        return value

    def validate_social(self, value):
        schema = from_json('social-schema.json')
        try:
            validate(value, schema, cls=Draft7Validator)
        except JsonSchemaValidationError as e:
            raise InvalidModelValueError(e)

        return value

    def update(self, instance, validated_data):
        assert isinstance(instance, OSFUser), 'instance must be a User'
        for attr, value in validated_data.items():
            if 'social' == attr:
                for key, val in value.items():
                    instance.social[key] = val
            elif 'accepted_terms_of_service' == attr:
                if value and not instance.accepted_terms_of_service:
                    instance.accepted_terms_of_service = timezone.now()
            elif 'default_region' == attr:
                user_settings = instance._settings_model('osfstorage').objects.get(owner=instance)
                user_settings.default_region_id = value
                user_settings.save()
                instance.default_region = self.context['request'].data['default_region']
            else:
                setattr(instance, attr, value)
        try:
            instance.save()
        except ValidationValueError as e:
            raise InvalidModelValueError(detail=e.message)
        except ValidationError as e:
            raise InvalidModelValueError(e)
        if set(validated_data.keys()).intersection(set(OSFUser.SPAM_USER_PROFILE_FIELDS.keys())):
            request_headers = string_type_request_headers(self.context['request'])
            instance.check_spam(saved_fields=validated_data, request_headers=request_headers)

        return instance

class UserAddonSettingsSerializer(JSONAPISerializer):
    """
    Overrides UserSerializer to make id required.
    """
    id = ser.CharField(source='config.short_name', read_only=True)
    user_has_auth = ser.BooleanField(source='has_auth', read_only=True)

    links = LinksField({
        'self': 'get_absolute_url',
        'accounts': 'account_links',
    })

    class Meta:
        @staticmethod
        def get_type(request):
            return get_kebab_snake_case_field(request.version, 'user-addons')

    def get_absolute_url(self, obj):
        return absolute_reverse(
            'users:user-addon-detail',
            kwargs={
                'provider': obj.config.short_name,
                'user_id': self.context['request'].parser_context['kwargs']['user_id'],
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def account_links(self, obj):
        # TODO: [OSF-4933] remove this after refactoring Figshare
        if hasattr(obj, 'external_accounts'):
            return {
                account._id: {
                    'account': absolute_reverse(
                        'users:user-external_account-detail', kwargs={
                            'user_id': obj.owner._id,
                            'provider': obj.config.short_name,
                            'account_id': account._id,
                            'version': self.context['request'].parser_context['kwargs']['version'],
                        },
                    ),
                    'nodes_connected': [n.absolute_api_v2_url for n in obj.get_attached_nodes(account)],
                }
                for account in obj.external_accounts.all()
            }
        return {}

class UserDetailSerializer(UserSerializer):
    """
    Overrides UserSerializer to make id required.
    """
    id = IDField(source='_id', required=True)


class ReadEmailUserDetailSerializer(UserDetailSerializer):

    email = ser.CharField(source='username', read_only=True)


class RelatedInstitution(JSONAPIRelationshipSerializer):
    id = ser.CharField(required=False, allow_null=True, source='_id')
    class Meta:
        type_ = 'institutions'

    def get_absolute_url(self, obj):
        return obj.absolute_api_v2_url


class UserInstitutionsRelationshipSerializer(BaseAPISerializer):

    data = ser.ListField(child=RelatedInstitution())
    links = LinksField({
        'self': 'get_self_url',
        'html': 'get_related_url',
    })

    def get_self_url(self, obj):
        return absolute_reverse(
            'users:user-institutions-relationship', kwargs={
                'user_id': obj['self']._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def get_related_url(self, obj):
        return absolute_reverse(
            'users:user-institutions', kwargs={
                'user_id': obj['self']._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def get_absolute_url(self, obj):
        return obj.absolute_api_v2_url

    class Meta:
        type_ = 'institutions'


class UserIdentitiesSerializer(JSONAPISerializer):
    id = IDField(source='_id', read_only=True)
    type = TypeField()
    external_id = ser.CharField(read_only=True)
    status = ser.CharField(read_only=True)

    links = LinksField({
        'self': 'get_absolute_url',
    })

    def get_absolute_url(self, obj):
        return absolute_reverse(
            'users:user-identities-detail',
            kwargs={
                'user_id': self.context['request'].parser_context['kwargs']['user_id'],
                'version': self.context['request'].parser_context['kwargs']['version'],
                'identity_id': obj['_id'],
            },
        )

    class Meta:
        type_ = 'external-identities'

class UserAccountExportSerializer(BaseAPISerializer):
    type = TypeField()

    class Meta:
        type_ = 'user-account-export-form'


class UserChangePasswordSerializer(BaseAPISerializer):
    type = TypeField()
    existing_password = ser.CharField(write_only=True, required=True)
    new_password = ser.CharField(write_only=True, required=True)

    class Meta:
        type_ = 'user_passwords'


class UserResetPasswordSerializer(BaseAPISerializer):
    uid = ser.CharField(write_only=True, required=True)
    token = ser.CharField(write_only=True, required=True)
    password = ser.CharField(write_only=True, required=True)

    class Meta:
        type_ = 'user_reset_password'


class ConfirmEmailTokenSerializer(BaseAPISerializer):
    uid = ser.CharField(write_only=True, required=True)
    destination = ser.CharField(write_only=True, required=True)
    token = ser.CharField(write_only=True, required=True)
    redirect_url = ser.CharField(read_only=True, required=False)

    class Meta:
        type_ = 'email_token_serializer'


class SanctionTokenSerializer(ConfirmEmailTokenSerializer):
    action = ser.ChoiceField(
        write_only=True,
        required=True,
        choices=[
            'approve',
            'reject',
        ],
    )
    sanction_type = ser.ChoiceField(
        write_only=True,
        required=False,
        choices=[
            'registration',
            'embargo',
            'embargo_termination_approval',
            'retraction',
        ],
    )

    class Meta:
        type_ = 'sanction_token_serializer'


class ExternalLoginSerialiser(BaseAPISerializer):
    email = ser.CharField(write_only=True, required=True)
    auth_user_fullname = ser.CharField(write_only=True, required=False)
    accepted_terms_of_service = ser.BooleanField(write_only=True, required=True)

    class Meta:
        type_ = 'external_login'


class UserSettingsSerializer(JSONAPISerializer):
    id = IDField(source='_id', read_only=True)
    type = TypeField()
    two_factor_enabled = ser.SerializerMethodField()
    two_factor_confirmed = ser.SerializerMethodField(read_only=True)
    subscribe_osf_general_email = ser.SerializerMethodField()
    subscribe_osf_help_email = ser.SerializerMethodField()
    deactivation_requested = ser.BooleanField(source='requested_deactivation', required=False)
    contacted_deactivation = ser.BooleanField(required=False, read_only=True)
    secret = ser.SerializerMethodField(read_only=True)

    def to_representation(self, instance):
        self.context['twofactor_addon'] = instance.get_addon('twofactor')
        return super().to_representation(instance)

    def get_two_factor_enabled(self, obj):
        try:
            two_factor = TwoFactorUserSettings.objects.get(owner_id=obj.id)
            return not two_factor.deleted
        except TwoFactorUserSettings.DoesNotExist:
            return False

    def get_two_factor_confirmed(self, obj):
        two_factor_addon = self.context['twofactor_addon']
        if two_factor_addon and two_factor_addon.is_confirmed:
            return True
        return False

    def get_secret(self, obj):
        two_factor_addon = self.context['twofactor_addon']
        if two_factor_addon and not two_factor_addon.is_confirmed:
            return two_factor_addon.totp_secret_b32

    def get_subscribe_osf_general_email(self, obj):
        return obj.mailchimp_mailing_lists.get(MAILCHIMP_GENERAL_LIST, False)

    def get_subscribe_osf_help_email(self, obj):
        return obj.osf_mailing_lists.get(OSF_HELP_LIST, False)

    links = LinksField({
        'self': 'get_absolute_url',
        'export': 'get_export_link',
    })

    def get_export_link(self, obj):
        return absolute_reverse(
            'users:user-account-export',
            kwargs={
                'user_id': obj._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def get_absolute_url(self, obj):
        return absolute_reverse(
            'users:user_settings',
            kwargs={
                'user_id': obj._id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    class Meta:
        @staticmethod
        def get_type(request):
            return get_kebab_snake_case_field(request.version, 'user-settings')


class UserSettingsUpdateSerializer(UserSettingsSerializer):
    id = IDField(source='_id', required=True)
    two_factor_enabled = ser.BooleanField(write_only=True, required=False)
    two_factor_verification = ser.IntegerField(write_only=True, required=False)
    subscribe_osf_general_email = ser.BooleanField(read_only=False, required=False)
    subscribe_osf_help_email = ser.BooleanField(read_only=False, required=False)

    # Keys represent field names values represent the human readable names stored in DB.
    MAP_MAIL = {
        'subscribe_osf_help_email': OSF_HELP_LIST,
        'subscribe_osf_general_email': MAILCHIMP_GENERAL_LIST,
    }

    def update_email_preferences(self, instance, attr, value):
        if self.MAP_MAIL[attr] == OSF_HELP_LIST:
            update_osf_help_mails_subscription(user=instance, subscribe=value)
        else:
            update_mailchimp_subscription(instance, self.MAP_MAIL[attr], value)
        instance.save()

    def update_two_factor(self, instance, value, two_factor_addon):
        if value:
            if not two_factor_addon:
                two_factor_addon = instance.get_or_add_addon('twofactor')
                two_factor_addon.save()
        else:
            auth = get_user_auth(self.context['request'])
            instance.delete_addon('twofactor', auth=auth)

        return two_factor_addon

    def verify_two_factor(self, instance, value, two_factor_addon):
        if not two_factor_addon:
            raise exceptions.ValidationError(detail='Two-factor authentication is not enabled.')
        if two_factor_addon.verify_code(value):
            two_factor_addon.is_confirmed = True
        else:
            raise exceptions.PermissionDenied(detail='The two-factor verification code you provided is invalid.')
        two_factor_addon.save()

    def request_deactivation(self, instance, requested_deactivation):

        if instance.requested_deactivation != requested_deactivation:
            instance.requested_deactivation = requested_deactivation
            if not requested_deactivation:
                instance.contacted_deactivation = False
            instance.save()

    def to_representation(self, instance):
        """
        Overriding to_representation allows using different serializers for the request and response.
        """
        context = self.context
        return UserSettingsSerializer(instance=instance, context=context).data

    def update(self, instance, validated_data):

        for attr, value in validated_data.items():
            if 'two_factor_enabled' == attr:
                two_factor_addon = instance.get_addon('twofactor')
                self.update_two_factor(instance, value, two_factor_addon)
            elif 'two_factor_verification' == attr:
                two_factor_addon = instance.get_addon('twofactor')
                self.verify_two_factor(instance, value, two_factor_addon)
            elif 'requested_deactivation' == attr:
                self.request_deactivation(instance, value)
            elif attr in self.MAP_MAIL.keys():
                self.update_email_preferences(instance, attr, value)

        return instance


class UserEmail:
    def __init__(self, email_id, address, confirmed, verified, primary, is_merge=False):
        self.id = email_id
        self.address = address
        self.confirmed = confirmed
        self.verified = verified
        self.primary = primary
        self.is_merge = is_merge


class UserEmailsSerializer(JSONAPISerializer):

    filterable_fields = frozenset([
        'confirmed',
        'verified',
        'primary',
    ])

    id = IDField(read_only=True)
    type = TypeField()
    email_address = ser.CharField(source='address')
    confirmed = ser.BooleanField(read_only=True, help_text='User has clicked the confirmation link in an email.')
    verified = ser.BooleanField(required=False, help_text='User has verified adding the email on the OSF, i.e. via a modal.')
    primary = ser.BooleanField(required=False)
    is_merge = ser.BooleanField(read_only=True, required=False, help_text='This unconfirmed email is already confirmed to another user.')
    links = LinksField({
        'self': 'get_absolute_url',
        'resend_confirmation': 'get_resend_confirmation_url',
    })

    def get_absolute_url(self, obj):
        user = self.context['request'].user
        return absolute_reverse(
            'users:user-email-detail',
            kwargs={
                'user_id': user._id,
                'email_id': obj.id,
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def get_resend_confirmation_url(self, obj):
        if not obj.confirmed:
            url = self.get_absolute_url(obj)
            return f'{url}?resend_confirmation=true'

    class Meta:
        @staticmethod
        def get_type(request):
            return get_kebab_snake_case_field(request.version, 'user-emails')

    def create(self, validated_data):
        user = self.context['request'].user
        address = validated_data['address']
        is_merge = Email.objects.filter(address=address).exists()
        if address in user.unconfirmed_emails or address in user.emails.all().values_list('address', flat=True):
            raise Conflict(f'This user already has registered with the email address {address}')
        try:
            token = user.add_unconfirmed_email(address)
            user.save()
            if CONFIRM_REGISTRATIONS_BY_EMAIL:
                send_confirm_email_async(user, email=address)
                user.email_last_sent = timezone.now()
                user.save()
        except ValidationError as e:
            raise exceptions.ValidationError(e.args[0])
        except BlockedEmailError:
            raise exceptions.ValidationError('This email address domain is blocked.')

        return UserEmail(email_id=token, address=address, confirmed=False, verified=False, primary=False, is_merge=is_merge)

    def update(self, instance, validated_data):
        user = self.context['request'].user
        primary = validated_data.get('primary', None)
        verified = validated_data.get('verified', None)
        if primary and instance.confirmed:
            user.username = instance.address
            user.save()
        elif primary and not instance.confirmed:
            raise exceptions.ValidationError('You cannot set an unconfirmed email address as your primary email address.')

        if verified and not instance.verified:
            if not instance.confirmed:
                raise exceptions.ValidationError('You cannot verify an email address that has not been confirmed by a user.')
            user.confirm_email(token=instance.id, merge=instance.is_merge)
            instance.verified = True
            instance.is_merge = False
            new_email = Email.objects.get(address=instance.address, user=user)
            instance.id = hashids.encode(new_email.id)
            user.save()

        return instance


class UserNodeSerializer(NodeSerializer):
    filterable_fields = NodeSerializer.filterable_fields | {'current_user_permissions'}


class UserMessageSerializer(JSONAPISerializer):
    """
    Serializer for creating and managing `UserMessage` instances.

    Attributes:
        message_text (CharField): The text content of the message.
        message_type (ChoiceField): The type of message being sent, restricted to `MessageTypes`.
        institution (RelationshipField): The institution related to the message. Required.
        user (RelationshipField): The recipient of the message.
    """
    id = IDField(read_only=True, source='_id')
    message_text = ser.CharField(
        required=True,
        help_text='The content of the message to be sent.',
    )
    message_type = ser.ChoiceField(
        choices=MessageTypes.choices,
        required=True,
        help_text='The type of message being sent. Must match one of the defined `MessageTypes`.',
    )
    institution = RelationshipField(
        related_view='institutions:institution-detail',
        related_view_kwargs={'institution_id': '<institution._id>'},
        help_text='The institution associated with this message.',
    )
    bcc_sender = ser.BooleanField(
        required=False,
        default=False,
        help_text='If true, BCCs the sender, giving them a copy of the email message they sent.',
    )
    reply_to = ser.BooleanField(
        default=False,
        help_text='Whether to set the sender\'s username as the `Reply-To` header in the email.',
    )

    def get_absolute_url(self, obj: UserMessage) -> str:
        return api_v2_url(
            'users:user-messages',
            params={
                'user_id': self.context['request'].parser_context['kwargs']['user_id'],
                'version': self.context['request'].parser_context['kwargs']['version'],
            },
        )

    def to_internal_value(self, data):
        institution_id = data.pop('institution', None)
        data = super().to_internal_value(data)
        if institution_id:
            data['institution'] = institution_id
        return data

    class Meta:
        type_ = 'user-messages'

    def create(self, validated_data: Dict[str, Any]) -> UserMessage:
        """
        Creates a `UserMessage` instance based on validated data.

        Args:
            validated_data (Dict[str, Any]): The data validated by the serializer.

        Raises:
            ValidationError: If required validations fail (e.g., sender not an institutional admin,
                             or recipient not affiliated with the institution).

        Returns:
            UserMessage: The created `UserMessage` instance.
        """
        request = self.context['request']
        sender = request.user

        recipient = get_object_or_error(
            OSFUser,
            self.context['view'].kwargs['user_id'],
            request,
            'user',
        )

        institution_id = validated_data.get('institution')
        if not institution_id:
            raise exceptions.ValidationError({'institution': 'Institution is required.'})

        institution = get_object_or_error(
            Institution,
            institution_id,
            request,
            'institution',
        )

        if not sender.is_institutional_admin_at(institution):
            raise Conflict({'sender': 'Only institutional administrators can create messages.'})

        if not recipient.is_affiliated_with_institution(institution):
            raise Conflict(
                {'user': 'Cannot send to a recipient that is not affiliated with the provided institution.'},
            )

        return UserMessage.objects.create(
            sender=sender,
            recipient=recipient,
            institution=institution,
            message_type=MessageTypes.INSTITUTIONAL_REQUEST,
            message_text=validated_data['message_text'],
            is_sender_BCCed=validated_data['bcc_sender'],
            reply_to=validated_data['reply_to'],
        )
