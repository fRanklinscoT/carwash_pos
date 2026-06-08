from rest_framework import serializers
from .models import WashService, Shift, WashOrder, CashDeduction, Tenant, OperatorProfile, SubscriptionTier
from rest_framework.validators import UniqueValidator
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.crypto import get_random_string

# Create your serializers here.

class WashServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WashService
        fields = ['id', 'name', 'price', 'description', 'is_active']

class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = ['id', 'attendant', 'opened_at', 'closed_at', 'is_closed']
        read_only_fields = ['opened_at', 'attendant']

class WashOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = WashOrder
        # Notice we include 'created_at' so Flutter can send its offline timestamp
        fields = [
            'id', 'shift', 'license_plate', 'services', 
            'total_amount', 'payment_method', 'is_voided', 'created_at'
        ]
class CashDeductionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CashDeduction
        # Including created_at for the same offline-sync reason as WashOrder
        fields = ['id', 'shift', 'amount', 'reason', 'created_at']

class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ['id', 'name', 'tier', 'operator_limit', 'is_active']
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'email']
class OperatorProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    tenant_name = serializers.CharField(source='tenant.name', read_only=True)

    class Meta:
        model = OperatorProfile
        fields = ['id', 'user', 'tenant_name', 'is_active']
class TerminalAuthRequestSerializer(serializers.Serializer):
    """Validates the raw incoming login payload fired from the Flutter terminal numpad."""
    passcode_pin = serializers.CharField(
        max_length=6, 
        min_length=6, 
        required=True,
        help_text="The 6-digit numeric operator token pin array code."
    )
class TenantOnboardingSerializer(serializers.Serializer):
    # Business Profile Fields
    company_name = serializers.CharField(
        max_length=255, 
        validators=[UniqueValidator(queryset=Tenant.objects.all())]
    )
    tier = serializers.ChoiceField(choices=SubscriptionTier.choices, default=SubscriptionTier.BASIC)
    operator_limit = serializers.IntegerField(required=False)

    # Employer Account Admin Fields
    username = serializers.CharField(
        max_length=150, 
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150, required=True)
    last_name = serializers.CharField(max_length=150, required=True)
    email = serializers.EmailField(
        validators=[UniqueValidator(queryset=User.objects.all())]
    )

    def create(self, validated_data):
        """
        Executes an atomic database transaction loop. 
        If user creation succeeds but tenant profiling fails, 
        the database automatically rolls back to prevent orphan accounts.
        """
        with transaction.atomic():
            # 1. Strip and build the Employer's core User Account
            user = User.objects.create_user(
                username=validated_data['username'],
                email=validated_data['email'],
                password=validated_data['password'],
                first_name=validated_data['first_name'],
                last_name=validated_data['last_name'],
                is_staff=True  # Allows the employer to access administrative workflows
            )

            # 2. Build the Multi-Tenant business container pinned to this user
            tenant_kwargs = {
                'name': validated_data['company_name'],
                'owner': user,
                'tier': validated_data['tier']
            }

            # If a custom negotiated limit was explicitly passed by sales, override defaults
            if 'operator_limit' in validated_data:
                tenant_kwargs['operator_limit'] = validated_data['operator_limit']

            tenant = Tenant.objects.create(**tenant_kwargs)
            
            # Formulate the programmatic response structure
            return {
                "tenant_id": tenant.id,
                "company_name": tenant.name,
                "tier": tenant.tier,
                "operator_limit": tenant.operator_limit,
                "employer_id": user.id,
                "username": user.username
            }
        
class OperatorCreationSerializer(serializers.ModelSerializer):
    """
    Handles extracting incoming Flutter payloads to provision both 
    a Django core User account and its companion OperatorProfile mapping.
    """
    username = serializers.CharField(max_length=150)
    first_name = serializers.CharField(max_length=150, required=True)
    email = serializers.EmailField(required=True, write_only=True)

    class Meta:
        model = OperatorProfile
        fields = ['id', 'username', 'first_name', 'email', 'passcode_pin']

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("An account with this username ID already exists.")
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        # Grab the active logged-in Employer Admin's business tenant container
        tenant = request.user.owned_tenant

        with transaction.atomic():
            # 1. Create the base User record (set a random unusable pass since they use 6-digit terminal PINs)
            user = User.objects.create_user(
                username=validated_data['username'],
                first_name=validated_data['first_name'],
                email=validated_data['email'],
                password=get_random_string(32)
            )

            # 2. Build the operator profile card inside the tenant space
            operator_profile = OperatorProfile.objects.create(
                user=user,
                tenant=tenant,
                passcode_pin=validated_data['passcode_pin']
            )
            
            return operator_profile
    def to_representation(self, instance):
        """
        Intercepts the outgoing serialization layer and reroutes it to 
        OperatorProfileSerializer, cleanly resolving nested User attributes.
        """
        return OperatorProfileSerializer(instance, context=self.context).data