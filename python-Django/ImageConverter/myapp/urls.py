from django.urls import path
from myapp.views import create_gif

urlpatterns = [
    # No trailing slash (per requirement)
    path("create-gif", create_gif, name="create_gif"),
]