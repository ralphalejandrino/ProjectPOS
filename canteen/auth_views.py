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
from .models import User
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
        # so admin-only actions (set_role) must be gated here too — not just on the decorator.
        if self.action in ['create', 'destroy', 'set_role']:
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
