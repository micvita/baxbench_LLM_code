from django.db import models

class Service(models.Model):
    service_id = models.CharField(max_length=255)
    token_hash = models.CharField(max_length=128)  # hex-encoded hash
    last_notification = models.DateTimeField()

    class Meta:
        unique_together = ("service_id", "token_hash")
        indexes = [
            models.Index(fields=["token_hash"]),
            models.Index(fields=["service_id", "token_hash"]),
        ]

    def __str__(self) -> str:
        return f"{self.service_id}"