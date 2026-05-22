from rest_framework import permissions

from .access import effective_pages


def HasPageAccess(page_key):
    """FEATURE-044: permission granting access when ``page_key`` is in the user's
    effective page set (role default + per-user override).

    Reads the live DB-backed user (request.user), NOT the JWT claim, so a
    revocation takes effect immediately. Behaviour is identical to the previous
    role gate for any account without an override, since defaults match roles.
    """
    class _HasPageAccess(permissions.BasePermission):
        def has_permission(self, request, view):
            user = request.user
            return bool(
                user
                and user.is_authenticated
                and page_key in effective_pages(user)
            )
    _HasPageAccess.__name__ = f'HasPageAccess_{page_key}'
    return _HasPageAccess


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
