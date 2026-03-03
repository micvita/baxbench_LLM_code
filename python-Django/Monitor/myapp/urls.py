from django.urls import path
from .views import monitor_commands

urlpatterns = [
    # IMPORTANT: no trailing slash
    path("monitor/commands", monitor_commands),
]