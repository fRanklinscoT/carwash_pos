from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from django.utils import timezone
from django.db import transaction
from .models import WashService, Shift, WashOrder, CashDeduction, OperatorProfile
from .serializers import WashServiceSerializer, ShiftSerializer, OperatorCreationSerializer,TenantOnboardingSerializer , WashOrderSerializer, CashDeductionSerializer, OperatorProfileSerializer, TerminalAuthRequestSerializer
from .permissions import IsPlatformSuperAdmin


class IsAdminOrReadOnly(permissions.BasePermission):
    """Custom permission: Attendants can only read, Admins can do full CRUD."""
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff

class WashServiceViewSet(viewsets.ModelViewSet):
    """
    Handles the service catalog.
    Attendants can view active services; Admins can manage them.
    """
    queryset = WashService.objects.filter(is_active=True)
    serializer_class = WashServiceSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class ShiftViewSet(viewsets.ModelViewSet):
    """
    Handles starting and ending work shifts.
    Automatically assigns the logged-in user as the attendant.
    """
    serializer_class = ShiftSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Admins see all shifts; attendants only see their own
        if self.request.user.is_staff:
            return Shift.objects.all().order_by('-opened_at')
        return Shift.objects.filter(attendant=self.request.user).order_by('-opened_at')

    def perform_create(self, serializer):
        # Check if user already has an active open shift
        active_shift = Shift.objects.filter(attendant=self.request.user, is_closed=False).first()
        if active_shift:
            return Response(
                {"error": "You already have an active shift open."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save(attendant=self.request.user)

    @action(detail=True, methods=['post'])
    def close_shift(self, request, pk=None):
        """Custom endpoint to close an active shift and lock the till."""
        shift = self.get_object()
        if shift.is_closed:
            return Response({"error": "Shift is already closed."}, status=status.HTTP_400_BAD_REQUEST)
        
        shift.is_closed = True
        shift.closed_at = timezone.now()
        shift.save()
        return Response({"status": "Shift successfully closed."})


class WashOrderViewSet(viewsets.ModelViewSet):
    """
    Handles transaction logging. 
    Protects data integrity by calculating prices server-side.
    """
    serializer_class = WashOrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return WashOrder.objects.filter(is_voided=False).order_by('-created_at')
        return WashOrder.objects.filter(shift__attendant=self.request.user, is_voided=False).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        """Overridden to calculate the true total cost using database constants."""
        data = request.data
        service_ids = data.get('services', [])
        
        if not service_ids:
            return Response({"error": "At least one service must be selected."}, status=status.HTTP_400_BAD_REQUEST)
        
        services = WashService.objects.filter(id__in=service_ids, is_active=True)
        if not services.exists():
            return Response({"error": "Invalid services selected."}, status=status.HTTP_400_BAD_REQUEST)
        
        total_amount = sum(service.price for service in services)
        
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        
        order = serializer.save(total_amount=total_amount)
        order.services.set(services)
        
        return Response(WashOrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='bulk-sync')
    def bulk_sync(self, request):
        """
        OFFLINE SYNC ENDPOINT: Ingests an array of locally cached 
        transactions from the Flutter queue when internet is restored.
        """
        orders_data = request.data.get('orders', [])
        saved_orders = []

        if not orders_data:
            return Response({"error": "No orders provided for sync."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():  # All sync or none sync to guarantee database consistency
            for order_data in orders_data:
                service_ids = order_data.get('services', [])
                services = WashService.objects.filter(id__in=service_ids)
                total_amount = sum(service.price for service in services)
                
                serializer = self.get_serializer(data=order_data)
                serializer.is_valid(raise_exception=True)
                
                # Explicitly preserves the client-side device timestamp passed in 'created_at'
                order = serializer.save(total_amount=total_amount)
                order.services.set(services)
                saved_orders.append(serializer.data)

        return Response({"status": "Sync successful", "synced_count": len(saved_orders)}, status=status.HTTP_201_CREATED)


class CashDeductionViewSet(viewsets.ModelViewSet):
    """Handles logging petty cash drawer payouts (e.g. lunch money)."""
    serializer_class = CashDeductionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return CashDeduction.objects.all().order_by('-created_at')
        return CashDeduction.objects.filter(shift__attendant=self.request.user).order_by('-created_at')

class TerminalPinAuthView(APIView):
    """
    Endpoint for POS tablet terminals. Checks the 6-digit passcode pin 
    and returns operator identity maps instantly without native web cookies.
    """
    permission_classes = [AllowAny] 

    def post(self, request, format=None):
        serializer = TerminalAuthRequestSerializer(data=request.data)
        
        if serializer.is_valid():
            pin = serializer.validated_data['passcode_pin']
            
            # Lookup operator profile tied to this exact passcode sequence
            try:
                operator = OperatorProfile.objects.select_related('user', 'tenant').get(
                    passcode_pin=pin, 
                    is_active=True,
                    tenant__is_active=True # Ensure the whole company subscription isn't locked/paused
                )
            except OperatorProfile.DoesNotExist:
                return Response(
                    {"detail": "Invalid terminal authentication PIN sequence."}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )

            # Execution block success payload mapping out identity tokens for Flutter
            profile_data = OperatorProfileSerializer(operator).data
            
            return Response({
                "message": "Terminal authorization verified successfully.",
                "operator": profile_data,
                "session_tokens": {
                    "access": "mock_generated_jwt_access_string_for_this_operator",
                    "refresh": "mock_generated_jwt_refresh_string_for_this_operator"
                }
            }, status=status.HTTP_200_OK)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class PlatformOnboardingView(APIView):
    """
    Platform Superadmin Controller.
    Provisions a new business Tenant alongside their main Employer Admin account.
    """
    permission_classes = [IsPlatformSuperAdmin] # Strictly locked to your superuser credentials

    def post(self, request, format=None):
        serializer = TenantOnboardingSerializer(data=request.data)
        if serializer.is_valid():
            result_payload = serializer.save()
            return Response({
                "status": "Tenant system provisioned cleanly.",
                "data": result_payload
            }, status=status.HTTP_201_CREATED)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class OperatorProfileViewSet(viewsets.ModelViewSet):
    """
    Management Control view for Employer Admins to list, 
    onboard, and toggle their active bay operators.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return OperatorCreationSerializer
        return OperatorProfileSerializer

    def get_queryset(self):
        # Safety check: ensure staff/employers only look at their own company's workers
        if hasattr(self.request.user, 'owned_tenant'):
            return OperatorProfile.objects.filter(tenant=self.request.user.owned_tenant).order_by('-id')
        return OperatorProfile.objects.none()

    def perform_create(self, serializer):
        # Triggers model clean handles to enforce the SubscriptionTier ceiling cap rules
        serializer.save()