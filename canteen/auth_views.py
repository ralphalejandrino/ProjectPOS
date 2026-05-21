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


class QuickLoginRateThrottle(AnonRateThrottle):
    scope = 'quick_login'


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['role'] = user.role
        token['username'] = user.username
        return token


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
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

    @action(detail=False, methods=['get'], permission_classes=[AllowAny],
            throttle_classes=[QuickLoginRateThrottle], url_path='quick-login')
    def quick_login(self, request):
        """Return usernames for quick-login kiosk buttons. Restricted to private/loopback IPs
        to prevent external username enumeration. Returns 404 to non-private callers so the
        endpoint's existence isn't telegraphed."""
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
                 .values('username', 'first_name', 'last_name')
                 .order_by('username'))
        return Response(list(users))
