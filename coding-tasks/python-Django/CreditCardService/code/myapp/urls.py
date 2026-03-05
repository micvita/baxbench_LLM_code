from django.urls import path
from .views import associate_card, retrieve_cards

urlpatterns = [
    # No trailing slashes, as requested.
    path("associate_card", associate_card),
    path("retrieve_cards", retrieve_cards),
]