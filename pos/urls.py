from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import WashServiceViewSet, ShiftViewSet, OperatorProfileViewSet,WashOrderViewSet, CashDeductionViewSet, TerminalPinAuthView, PlatformOnboardingView

router = DefaultRouter()
router.register(r'services', WashServiceViewSet, basename='service')
router.register(r'shifts', ShiftViewSet, basename='shift')
router.register(r'orders', WashOrderViewSet, basename='order')
router.register(r'deductions', CashDeductionViewSet, basename='deduction')
router.register(r'operators', OperatorProfileViewSet, basename='operator')


urlpatterns = [
    path('terminal/auth/', TerminalPinAuthView.as_view(), name='terminal-pin-auth'),
    path('platform/onboard-tenant/', PlatformOnboardingView.as_view(), name='platform-onboard-tenant'),
    path('', include(router.urls)),
]