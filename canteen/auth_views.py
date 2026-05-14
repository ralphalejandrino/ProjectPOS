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
from .permissions import IsManagerOrAbove


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

    @action(detail=False, methods=['get'], permission_classes=[AllowAny], url_path='quick-login')
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
