class AdminAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin/'):
            ip = request.META.get('REMOTE_ADDR', '')
            if ip not in ('127.0.0.1', '::1'):
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden('Admin access restricted.')
        return self.get_response(request)
