import json
from django.db import IntegrityError
from django.db.models import Count
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from .models import CreditCard, PhoneNumber, CardPhoneAssociation


def _json_error(message: str, status: int):
    return JsonResponse({"error": message}, status=status)


def _parse_json_body(request):
    try:
        raw = request.body.decode("utf-8") if request.body else ""
        if not raw:
            return None, "Empty body"
        return json.loads(raw), None
    except UnicodeDecodeError:
        return None, "Body must be valid UTF-8"
    except json.JSONDecodeError:
        return None, "Body must be valid JSON"


@csrf_exempt
def associate_card(request):
    if request.method != "POST":
        return _json_error("Method not allowed", 400)

    data, err = _parse_json_body(request)
    if err:
        return _json_error(err, 400)

    credit_card = data.get("credit_card")
    phone = data.get("phone")

    if not isinstance(credit_card, str) or not credit_card.strip():
        return _json_error("credit_card must be a non-empty string", 400)
    if not isinstance(phone, str) or not phone.strip():
        return _json_error("phone must be a non-empty string", 400)

    credit_card = credit_card.strip()
    phone = phone.strip()

    card_obj, _ = CreditCard.objects.get_or_create(number=credit_card)
    phone_obj, _ = PhoneNumber.objects.get_or_create(number=phone)

    try:
        CardPhoneAssociation.objects.get_or_create(credit_card=card_obj, phone=phone_obj)
    except IntegrityError:
        # In case of rare race conditions, treat as already-created.
        pass

    # Spec only requires a 201 with description; returning empty body is OK.
    return HttpResponse(status=201)


@csrf_exempt
def retrieve_cards(request):
    if request.method != "POST":
        return _json_error("Method not allowed", 400)

    data, err = _parse_json_body(request)
    if err:
        return _json_error(err, 400)

    phone_numbers = data.get("phone_numbers")
    if not isinstance(phone_numbers, list) or not phone_numbers:
        return _json_error("phone_numbers must be a non-empty array of strings", 400)

    cleaned = []
    for p in phone_numbers:
        if not isinstance(p, str) or not p.strip():
            return _json_error("phone_numbers must contain only non-empty strings", 400)
        cleaned.append(p.strip())

    # Use unique phones for "must match all".
    required_phones = sorted(set(cleaned))
    required_count = len(required_phones)

    # Find cards that are associated with ALL required phone numbers.
    qs = (
        CreditCard.objects.filter(associations__phone__number__in=required_phones)
        .annotate(matched_phones=Count("associations__phone", distinct=True))
        .filter(matched_phones=required_count)
        .values_list("number", flat=True)
        .distinct()
    )

    cards = list(qs)
    if not cards:
        return _json_error("Not found", 404)

    return JsonResponse({"card_numbers": cards}, status=200)