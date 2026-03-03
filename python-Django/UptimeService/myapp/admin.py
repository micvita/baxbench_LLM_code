from django.contrib import admin
from .models import Service

@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("service_id", "last_notification", "token_hash")
    search_fields = ("service_id", "token_hash")
    ordering = ("service_id",)