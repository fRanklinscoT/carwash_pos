from rest_framework import permissions

class IsPlatformSuperAdmin(permissions.BasePermission):
    """
    Strict permission constraint: Only allows global system superusers 
    (Illumidev) to execute creation strings.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)