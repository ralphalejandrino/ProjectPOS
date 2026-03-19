from rest_framework import permissions

class IsAdmin(permissions.BasePermission):
    """
    Allows access only to admins.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == 'admin'
        )

class IsManagerOrAbove(permissions.BasePermission):
    """
    Allows access only to managers or admins.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role in ['manager', 'admin']
        )

class IsCashierOrAbove(permissions.BasePermission):
    """
    Allows access only to cashiers, managers, or admins.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role in ['cashier', 'manager', 'admin']
        )
