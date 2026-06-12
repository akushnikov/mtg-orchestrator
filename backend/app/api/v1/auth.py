import hashlib
import hmac
import json
import time
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.utils import get_authorization_scheme_param

from app.config import settings


class TMAHTTPBearer(HTTPBearer):
    """Accept Authorization: tma <initData> and return 403 for all auth failures."""

    def make_not_authenticated_error(self) -> HTTPException:
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials:
        authorization = request.headers.get("Authorization")
        scheme, credentials = get_authorization_scheme_param(authorization)
        if not (authorization and scheme and credentials) or scheme.lower() != "tma":
            raise self.make_not_authenticated_error()
        return HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)


_bearer = TMAHTTPBearer(scheme_name="tma", auto_error=True)


def _validate_init_data(raw: str, bot_token: str | None = None) -> dict:
    token = settings.bot_token if bot_token is None else bot_token
    params = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        raise ValueError("Missing hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError("Invalid HMAC")

    auth_date = int(params.get("auth_date", 0))
    if time.time() - auth_date > 300:
        raise ValueError("auth_date expired")

    if "user" in params:
        params["user"] = json.loads(params["user"])
    return params


async def require_owner(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> int:
    if settings.dev_mock_init_data:
        if settings.bot_token:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return settings.owner_user_id

    try:
        params = _validate_init_data(credentials.credentials)
        user = params.get("user", {})
        user_id = int(user.get("id", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN) from None

    if user_id != settings.owner_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return user_id


OwnerUserID = Annotated[int, Depends(require_owner)]
