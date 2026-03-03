from django.contrib import admin
from .models import CreditCard, PhoneNumber, CardPhoneAssociation

admin.site.register(CreditCard)
admin.site.register(PhoneNumber)
admin.site.register(CardPhoneAssociation)