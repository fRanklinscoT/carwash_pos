from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Tenant, OperatorProfile

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    # What columns show up in the main list view grid
    list_display = ('name', 'owner', 'tier', 'operator_limit', 'is_active', 'created_at')
    
    # Sidebar quick-filters to drill down into tiers or active status
    list_filter = ('tier', 'is_active')
    
    # Search box functionality targeting the business name or the owner's user account
    search_fields = ('name', 'owner__username', 'owner__email')
    
    # Keeps creation date cleanly organized and immutable
    readonly_fields = ('created_at',)


@admin.register(OperatorProfile)
class OperatorProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'passcode_pin', 'is_active')
    list_filter = ('tenant', 'is_active')
    search_fields = ('user__username', 'user__first_name', 'tenant__name', 'passcode_pin')