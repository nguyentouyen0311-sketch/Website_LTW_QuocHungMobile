from catalog.models import SiteBranding


def site_branding(request):
    return {'site_branding': SiteBranding.objects.order_by('-updated_at').first()}
