from django.db import models

class CreditCard(models.Model):
    number = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        return self.number


class PhoneNumber(models.Model):
    number = models.CharField(max_length=32, unique=True)

    def __str__(self) -> str:
        return self.number


class CardPhoneAssociation(models.Model):
    credit_card = models.ForeignKey(CreditCard, on_delete=models.CASCADE, related_name="associations")
    phone = models.ForeignKey(PhoneNumber, on_delete=models.CASCADE, related_name="associations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["credit_card", "phone"], name="uniq_card_phone")
        ]

    def __str__(self) -> str:
        return f"{self.credit_card.number} <-> {self.phone.number}"