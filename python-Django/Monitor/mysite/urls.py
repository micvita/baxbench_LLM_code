from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin", admin.site.urls),   # no trailing slash
    path("", include("myapp.urls")),
]