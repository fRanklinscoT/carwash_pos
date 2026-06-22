from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator, MaxLengthValidator
from django.core.exceptions import ValidationError

# Create your models here.

class WashService(models.Model):
    """The Catalog: Defines what you sell, for how much, and what's included."""
    
    code = models.CharField(max_length=50, unique=True, default="p_custom", help_text="Unique identifier (e.g., p_valet)")
    name = models.CharField(max_length=100, help_text="Primary package name (e.g., Normal Wash & Go)")
    sub_name = models.CharField(max_length=100, blank=True, help_text="Secondary label (e.g., Standard Wash)")
    base_price = models.DecimalField(max_digits=8, decimal_places=2, help_text="Starting price in ZAR",default=0.00)
    features = models.TextField(blank=True, help_text="Comma-separated list of included features")

    is_active = models.BooleanField(default=True, help_text="Uncheck to hide this package from the tablets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['base_price']
        verbose_name = "Wash Service Package"
        verbose_name_plural = "Wash Service Packages"

    def __str__(self):
        return f"{self.name} - R{self.base_price}"
    def feature_list(self):
        if not self.features:
            return []
        return [f.strip() for f in self.features.split(',') if f.strip()]


class Shift(models.Model):
    """Cash Drawer Management: Tracks who is responsible for the till."""
    attendant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='shifts')
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    is_closed = models.BooleanField(default=False)

    def __str__(self):
        status = "CLOSED" if self.is_closed else "ACTIVE"
        return f"Shift: {self.attendant.username} - {self.opened_at.strftime('%Y-%m-%d')} ({status})"


class WashOrder(models.Model):
    """The Ledger: The core transaction record."""
    PAYMENT_CHOICES = [
        ('CASH', 'Cash'),
        ('CARD', 'Card (Speedpoint)'),
        ('TRANSFER', 'Bank Transfer / EFT'),
    ]

    # Linked to the active shift for end-of-day reconciliation
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name='orders')
    
    license_plate = models.CharField(max_length=20, blank=True, null=True)
    services = models.ManyToManyField(WashService)
    
    total_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0.00)
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES)
    
    # Audit Trail: Soft deletes for canceled tickets
    is_voided = models.BooleanField(default=False)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, 
        blank=True, null=True, related_name='voided_orders'
    )
    created_at = models.DateTimeField(default=timezone.now) 
    synced_at = models.DateTimeField(auto_now_add=True) 

    def __str__(self):
        return f"Order #{self.id} - {self.license_plate or 'No Plate'} (R{self.total_amount})"

class CashDeduction(models.Model):
    """Petty Cash: Tracks money taken out of the physical till (e.g., lunch, supplies)."""
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name='deductions')
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now)
    synced_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"-R{self.amount} ({self.reason})"

class SubscriptionTier(models.TextChoices):
    BASIC = 'BASIC', 'Basic Package (Self-Serve)'
    GROWTH = 'GROWTH', 'Growth Package (Scale-Up)'
    ENTERPRISE = 'ENTERPRISE', 'Enterprise (Sales Negotiated)'

class Tenant(models.Model):
    name = models.CharField(max_length=255, unique=True)
    owner = models.OneToOneField(User, on_delete=models.PROTECT, related_name='owned_tenant')
    tier = models.CharField(max_length=20, choices=SubscriptionTier.choices, default=SubscriptionTier.BASIC)
    
    # The dynamic database ceiling field
    operator_limit = models.PositiveIntegerField(
        default=5, 
        help_text="The absolute maximum number of active operator accounts allowed for this business entity."
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        """
        Lifecycle hook: Automatically provision default package caps on initial creation,
        but leave it completely open for manual adjustments or Enterprise sales overrides later.
        """
        if not self.pk:  
            if self.tier == SubscriptionTier.BASIC:
                self.operator_limit = 5
            elif self.tier == SubscriptionTier.GROWTH:
                self.operator_limit = 20
            elif self.tier == SubscriptionTier.ENTERPRISE:
                # Set a safe initial custom baseline, which sales can upscale in Django Admin instantly
                self.operator_limit = 30 
                
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} | Tier: {self.tier} (Limit: {self.operator_limit})"

class OperatorProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='operator_profile')
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='operators')
    
    # Custom 6-digit pin optimized for our Flutter card-flip view
    passcode_pin = models.CharField(
        max_length=6,
        validators=[MinLengthValidator(6), MaxLengthValidator(6)],
        unique=True,
        help_text="Exact 6-digit numeric PIN for terminal station authentication."
    )
    is_active = models.BooleanField(default=True)

    def clean(self):
        """
        The Gatekeeper validation loop checks the database-driven operator_limit 
        before letting an employer onboard a new user.
        """
        if not self.pk:
            active_operator_count = OperatorProfile.objects.filter(
                tenant=self.tenant, 
                is_active=True
            ).count()
            
            if active_operator_count >= self.tenant.operator_limit:
                raise ValidationError({
                    'tenant': f"Subscription limit reached. Your current plan tier limits your profile footprint to {self.tenant.operator_limit} active operators. Please upgrade."
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.tenant.name}] {self.user.first_name or self.user.username}"

class VehicleType(models.Model):
    code = models.CharField(max_length=50, unique=True, help_text="Unique identifier (e.g., v_sedan)")
    name = models.CharField(max_length=100, help_text="Display name on the POS tablet")
    base_price_modifier = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        help_text="Extra charge added to base wash price (ZAR)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck this to instantly hide this vehicle type from all tablets"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['base_price_modifier'] # Automatically sorts from smallest to largest vehicle
        verbose_name = "Vehicle Type"
        verbose_name_plural = "Vehicle Types"

    def __str__(self):
        return f"{self.name} (+R{self.base_price_modifier})"