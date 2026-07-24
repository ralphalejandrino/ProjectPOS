from django.contrib.auth import login, logout
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.db.models import Q
from .models import User, PosTransaction, Shift, ZReport
from .serializers import UserSerializer, UserCreateSerializer, LoginSerializer
from rest_framework.throttling import AnonRateThrottle
from .permissions import IsManagerOrAbove, IsAdmin
from . import access


class QuickLoginRateThrottle(AnonRateThrottle):
    """Throttle for the kiosk quick-login grid fetch.

    FLAG-072: the kiosk legitimately re-fetches the avatar grid on every page
    reload, so a tight per-minute cap on LOCAL requests makes the grid 429 →
    the frontend's `if (!response.ok) return;` silently drops it. The endpoint
    is already restricted to private/loopback callers (FLAG-039), which is the
    real enumeration protection — so skip throttling for local/loopback callers
    while keeping it for any remote caller that somehow reaches here.
    """
    scope = 'quick_login'

    def allow_request(self, request, view):
        import ipaddress
        remote = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR', ''))
        try:
            ip = ipaddress.ip_address(remote)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return True
        except ValueError:
            pass
        return super().allow_request(request, view)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role
        token['username'] = user.username
        # FEATURE-044: effective page set for client nav/guards. Server-side
        # enforcement reads the DB (not this claim), so a stale claim after a
        # change can never grant access beyond what the live record allows.
        token['pages'] = sorted(access.effective_pages(user))
        return token


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    # FLAG-042 — the PIN/password auth POST. AllowAny (the caller has no token
    # before authenticating) but brute-force-capped below: 10 failed attempts
    # per (ip, username) within a 15-minute window → 429 on the 11th. The window
    # is intentionally stricter than a flat 10/minute throttle, and the counter
    # resets on a successful login so legitimate users are never locked out by
    # their own typos once they get in.
    #
    # Prefer X-Real-IP set by nginx over REMOTE_ADDR (which is always 127.0.0.1
    # behind a local reverse proxy). Fall back to X-Forwarded-For, then REMOTE_ADDR.
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = (request.META.get('HTTP_X_REAL_IP')
          or (forwarded_for.split(',')[0].strip() if forwarded_for else None)
          or request.META.get('REMOTE_ADDR', 'unknown'))
    username = request.data.get('username', '')
    cache_key = f'login_attempts:{ip}:{username}'
    attempts = cache.get(cache_key, 0)
    if attempts >= 10:
        return Response(
            {'error': 'Too many login attempts. Try again in 15 minutes.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )

    serializer = LoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data
        login(request, user)
        cache.delete(cache_key)  # Reset on success
        user_data = UserSerializer(user).data
        user_data.pop('phone', None)
        user_data.pop('email', None)
        return Response({
            'success': True,
            'user': user_data,
            'message': 'Login successful'
        })
    # Increment failed attempts
    cache.set(cache_key, attempts + 1, timeout=900)  # 15 min window
    # Extract a readable error message from serializer errors
    errors = serializer.errors
    if 'non_field_errors' in errors:
        detail = errors['non_field_errors'][0]
    else:
        detail = next(iter(errors.values()))[0] if errors else 'Invalid credentials'
    return Response({
        'success': False,
        'detail': str(detail)
    }, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    try:
        refresh_token = request.data.get('refresh')
        if refresh_token:
            token = RefreshToken(refresh_token)
            token.blacklist()
    except TokenError:
        pass  # already blacklisted or invalid — still complete logout
    logout(request)
    return Response({'message': 'Logout successful'})

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_user(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)

class UserViewSet(viewsets.ModelViewSet):
    permission_classes = [IsManagerOrAbove]

    def get_permissions(self):
        # Overriding get_permissions takes precedence over @action(permission_classes=...),
        # so EVERY action's permission must be declared here too — not just on the decorator.
        # quick_login is the pre-login kiosk grid fetch and must stay AllowAny; without this
        # branch it fell through to IsManagerOrAbove and 401'd the unauthenticated fetch,
        # silently hiding the avatar grid (ISSUE-113 regression from QA-S3's admin gating).
        if self.action == 'quick_login':
            return [AllowAny()]
        if self.action in ['create', 'destroy', 'rename', 'set_role']:
            return [IsAdmin()]
        return [IsManagerOrAbove()]

    def get_queryset(self):
        return User.objects.filter(role__in=['cashier', 'manager']).order_by('username')

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        return UserSerializer

    def perform_update(self, serializer):
        password = self.request.data.get('password')
        instance = serializer.save()
        if password and len(password) >= 6:
            instance.set_password(password)
            instance.save()

    @action(detail=True, methods=['patch'], permission_classes=[IsAdmin], url_path='rename')
    def rename(self, request, pk=None):
        """FEATURE-041: admin-only account rename. Target is resolved through the
        managed (cashier/manager) queryset, so admins can't be renamed laterally.

        Safe because the username is purely the login label: every FK references the
        user PK (not the username string) and JWTs key off the user id, so no related
        rows or tokens need migrating when the username changes. Enforces
        case-insensitive uniqueness across the whole User table; optional
        first_name/last_name update the display name."""
        user = self.get_object()
        new_username = str(request.data.get('username', '')).strip()
        if not new_username:
            return Response({'error': 'Username cannot be blank.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(new_username) > 150:
            return Response({'error': 'Username too long (max 150 characters).'},
                            status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
            return Response({'error': f'Username "{new_username}" is already taken.'},
                            status=status.HTTP_409_CONFLICT)
        fields = ['username']
        user.username = new_username
        if 'first_name' in request.data:
            user.first_name = str(request.data.get('first_name', '')).strip()
            fields.append('first_name')
        if 'last_name' in request.data:
            user.last_name = str(request.data.get('last_name', '')).strip()
            fields.append('last_name')
        user.save(update_fields=fields)
        return Response({'id': user.pk, 'username': user.username,
                         'first_name': user.first_name, 'last_name': user.last_name})

    def destroy(self, request, *args, **kwargs):
        """FEATURE-041: admin-only hard delete (distinct from Deactivate/soft path).

        Resolved against the full User table (like set_role) so the safety messages
        are accurate rather than a bare 404. Blocks three cases:
          - deleting your own account (lockout),
          - deleting the last active admin (lockout),
          - deleting any user with transaction/shift/Z-report history.
        The history guard is the audit safeguard: Shift.cashier is CASCADE and
        Attendance.employee is CASCADE (would silently destroy those records),
        PosTransaction FKs are SET_NULL (would orphan transactions), and
        ZReport.cashier is PROTECT (would error). BLOCK preserves audit integrity
        with zero mutation; such accounts must be Deactivated instead."""
        try:
            user = User.objects.get(pk=kwargs.get('pk'))
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        if user.pk == request.user.pk:
            return Response({'error': 'You cannot delete your own account.'},
                            status=status.HTTP_403_FORBIDDEN)
        if (user.role == 'admin' and not User.objects
                .filter(role='admin', is_active=True).exclude(pk=user.pk).exists()):
            return Response({'error': 'Cannot delete the last active admin.'},
                            status=status.HTTP_403_FORBIDDEN)
        has_history = (
            PosTransaction.objects.filter(
                Q(cashier=user) | Q(voided_by=user) | Q(created_by=user)).exists()
            or Shift.objects.filter(cashier=user).exists()
            or ZReport.objects.filter(cashier=user).exists()
        )
        if has_history:
            return Response(
                {'error': 'This account has transaction, shift, or Z-report history and '
                          'cannot be deleted. Deactivate it instead to preserve audit records.'},
                status=status.HTTP_409_CONFLICT,
            )
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['patch'], permission_classes=[IsAdmin], url_path='role')
    def set_role(self, request, pk=None):
        """Admin-only role assignment. Rejects unknown roles (400) and self-demotion
        of the last admin path (403) to prevent lockout. Target is looked up against
        the full User table — not the cashier/manager-filtered queryset — so an admin
        targeting their own id gets the 403 safety message rather than a 404."""
        valid_roles = [choice[0] for choice in User.ROLE_CHOICES]
        new_role = str(request.data.get('role', '')).strip()
        if new_role not in valid_roles:
            return Response(
                {'error': f'Invalid role. Choose from: {", ".join(valid_roles)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
        if user.pk == request.user.pk and new_role != 'admin':
            return Response(
                {'error': 'You cannot change your own admin role (prevents lockout).'},
                status=status.HTTP_403_FORBIDDEN,
            )
        user.role = new_role
        user.save(update_fields=['role'])
        return Response({'id': user.pk, 'username': user.username, 'role': user.role})

    @action(detail=True, methods=['patch'], permission_classes=[IsManagerOrAbove],
            url_path='reset-password')
    def reset_password(self, request, pk=None):
        """FEATURE-006: admin/manager resets another user's password.

        Permission is manager-or-above (cashier denied) — note get_permissions()
        leaves this action on the IsManagerOrAbove default, so it is NOT in the
        IsAdmin set with create/destroy/set_role. The target is resolved through
        get_object() against the managed queryset (cashier/manager only), so admins
        cannot be targeted (404) — preventing lateral takeover and privilege
        escalation. Only the password is touched here; role is never read, so this
        flow cannot change a role."""
        user = self.get_object()
        password = str(request.data.get('password', ''))
        if len(password) < 6:
            return Response(
                {'error': 'Password must be at least 6 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(password)
        user.save(update_fields=['password'])
        return Response({'id': user.pk, 'username': user.username, 'detail': 'Password reset.'})

    @action(detail=False, methods=['get'], url_path='page-access-catalog')
    def page_access_catalog(self, request):
        """FEATURE-044: the page-access toggle catalog for the Manage modal.

        Returns every gateable page (key + label + sensitivity) and the subset
        the REQUESTER may actually toggle (grantable). The UI disables the rest,
        but the server is authoritative — set_page_access re-checks grantability.
        """
        return Response({
            'pages': [
                {'key': k, 'label': access.PAGE_LABELS[k], 'sensitive': k in access.SENSITIVE_PAGES}
                for k in access.GATEABLE_PAGES
            ],
            'grantable': sorted(access.grantable_pages(request.user)),
        })

    @action(detail=True, methods=['patch'], url_path='page-access')
    def set_page_access(self, request, pk=None):
        """FEATURE-044: set a target user's per-page access (manager/admin only).

        GUARDRAIL (the security boundary — do not weaken): the requester may only
        flip pages within their own grantable set (access.grantable_pages); bits
        outside that set are preserved from the target's current effective set, so
        a manager can neither grant a sensitive/admin-only page (e.g. Settings)
        nor any page beyond their own — no escalation path. Target is resolved via
        the managed (cashier/manager) queryset, so admins can't be retargeted.
        """
        user = self.get_object()
        requested = request.data.get('pages', [])
        if not isinstance(requested, list):
            return Response({'error': 'pages must be a list of page keys.'},
                            status=status.HTTP_400_BAD_REQUEST)
        unknown = [p for p in requested if p not in access.GATEABLE_PAGES]
        if unknown:
            return Response({'error': f'Unknown page(s): {", ".join(map(str, unknown))}'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Reject (rather than silently drop) an attempt to set a page the
        # requester may not grant — surfaces escalation attempts instead of
        # masking them. Bits the requester DOESN'T touch are still preserved.
        manageable = access.grantable_pages(request.user)
        current = access.effective_pages(user) & set(access.GATEABLE_PAGES)
        requested_set = set(requested)
        attempted_changes = (requested_set ^ current)  # adds or removes
        if attempted_changes - manageable:
            forbidden = sorted(attempted_changes - manageable)
            return Response(
                {'error': f'You are not allowed to change access to: {", ".join(forbidden)}'},
                status=status.HTTP_403_FORBIDDEN,
            )
        user.allowed_pages = access.resolve_page_access(request.user, user, requested)
        user.save(update_fields=['allowed_pages'])
        return Response({
            'id': user.pk,
            'username': user.username,
            'allowed_pages': user.allowed_pages,
            'effective_pages': sorted(access.effective_pages(user)),
        })

    @action(detail=False, methods=['get'], permission_classes=[AllowAny],
            throttle_classes=[QuickLoginRateThrottle], url_path='quick-login')
    def quick_login(self, request):
        """Return the pre-login kiosk avatar grid (cashier/manager display labels).

        FLAG-042 — intentional AllowAny, defence-in-depth against enumeration:
          * AllowAny is REQUIRED: the cashier has no token yet when the login
            screen needs to draw the grid, so this fetch is necessarily
            unauthenticated. The design is loopback/LAN-only — FLAG-039 blocks
            Tailscale/remote access at the network edge, and this view ALSO
            returns 404 to any non-private caller (below) so the endpoint's
            existence isn't telegraphed off-LAN.
          * Throttled (QuickLoginRateThrottle) to mitigate enumeration: remote
            callers that somehow reach the view are rate-limited; local/loopback
            kiosk reloads are exempt (FLAG-072) because the IP guard is their
            real protection and a per-minute cap would 429 the grid on reload.
          * Minimal payload (FLAG-042): only ``username`` (the login identifier
            the kiosk fills into the form — the POST authenticates
            username+password) and ``first_name`` (the display label). No
            ``last_name``/``email``/``phone``/PII is returned, so a LAN caller
            can't harvest a staff roster of full names from this endpoint.
        """
        import ipaddress
        remote = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR', ''))
        try:
            ip = ipaddress.ip_address(remote)
            if not (ip.is_private or ip.is_loopback or ip.is_link_local):
                return Response(status=status.HTTP_404_NOT_FOUND)
        except ValueError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        users = (User.objects
                 .filter(is_active=True, role__in=['cashier', 'manager'])
                 .values('username', 'first_name')
                 .order_by('username'))
        return Response(list(users))
