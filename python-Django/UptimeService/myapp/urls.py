from django.urls import path
from . import views

urlpatterns = [
    # IMPORTANT: no trailing slashes
    path("heartbeat", views.heartbeat),
    path("services", views.services),
]