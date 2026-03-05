import json
import hashlib
import hmac
from typing import Any, Dict, Tuple

from django.conf import settings
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Service


def _json_body(request: HttpRequest) -> Tuple[Dict[str, Any] | None, JsonResponse | None]:
    try:
        raw = request.body.decode("utf-8") if request.body is not None else ""
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return None, JsonResponse({"error": "JSON body must be an object."}, status=400)
        return data, None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JsonResponse({"error": "Invalid JSON."}, status=400)


def _token_hash(token: str) -> str:
    # Deterministic hash using APP_SECRET as salt/pepper.
    # Stored as hex string; enough for lookup/filtering without keeping raw token.
    salt = settings.APP_SECRET.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 120_000, dklen=32)
    return dk.hex()


def _validate_required_str(data: Dict[str, Any], key: str) -> Tuple[str | None, JsonResponse | None]:
    val = data.get(key)
    if not isinstance(val, str) or not val.strip():
        return None, JsonResponse({"error": f"'{key}' is required and must be a non-empty string."}, status=400)
    return val, None


@csrf_exempt
@require_POST
def heartbeat(request: HttpRequest) -> HttpResponse:
    data, err = _json_body(request)
    if err:
        return err

    service_id, e1 = _validate_required_str(data, "serviceId")
    if e1:
        return e1
    token, e2 = _validate_required_str(data, "token")
    if e2:
        return e2

    th = _token_hash(token)
    now = timezone.now()

    # Upsert per (service_id, token_hash)
    Service.objects.update_or_create(
        service_id=service_id,
        token_hash=th,
        defaults={"last_notification": now},
    )

    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
@require_POST
def services(request: HttpRequest) -> HttpResponse:
    data, err = _json_body(request)
    if err:
        return err

    token, e = _validate_required_str(data, "token")
    if e:
        return e

    th = _token_hash(token)

    qs = Service.objects.filter(token_hash=th).order_by("service_id")
    payload = [
        {
            "serviceId": s.service_id,
            "lastNotification": s.last_notification.isoformat().replace("+00:00", "Z"),
        }
        for s in qs
    ]
    return JsonResponse(payload, safe=False, status=200)