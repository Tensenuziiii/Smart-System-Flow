"""JWT authentication and security utilities."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)


class TokenData(BaseModel):
    sub: str
    tenant_id: str
    exp: Optional[datetime] = None


def create_access_token(sub: str, tenant_id: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": sub, "tenant_id": tenant_id, "exp": expire, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return TokenData(
            sub=payload["sub"],
            tenant_id=payload["tenant_id"],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_tenant(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> TokenData:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(credentials.credentials)


async def _verify_oidc_id_token(token: str, discovery_url: str, client_id: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        claims = jwt.get_unverified_claims(token)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid provider token") from exc

    if header.get("alg") != "RS256" or not header.get("kid"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unsupported provider token")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            discovery_response = await client.get(discovery_url)
            discovery_response.raise_for_status()
            discovery = discovery_response.json()
            jwks_response = await client.get(discovery["jwks_uri"])
            jwks_response.raise_for_status()
            keys = jwks_response.json().get("keys", [])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not verify provider token") from exc

    signing_key = next((key for key in keys if key.get("kid") == header["kid"]), None)
    if signing_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown provider signing key")

    try:
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=discovery["issuer"],
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except (JWTError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid provider token") from exc


async def verify_google_id_token(token: str, client_id: str) -> dict:
    claims = await _verify_oidc_id_token(
        token,
        "https://accounts.google.com/.well-known/openid-configuration",
        client_id,
    )
    if not claims.get("email") or claims.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google account email is not verified")
    return claims


async def verify_microsoft_id_token(token: str, client_id: str, tenant_id: str) -> dict:
    try:
        tenant_claim = jwt.get_unverified_claims(token).get("tid", "")
        tenant_guid = str(uuid.UUID(tenant_claim))
    except (JWTError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Microsoft tenant") from exc

    configured_tenant = tenant_id.lower()
    if configured_tenant not in {"common", "organizations", "consumers"}:
        try:
            if str(uuid.UUID(configured_tenant)) != tenant_guid:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unexpected Microsoft tenant")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invalid Microsoft tenant configuration") from exc

    claims = await _verify_oidc_id_token(
        token,
        f"https://login.microsoftonline.com/{tenant_guid}/v2.0/.well-known/openid-configuration",
        client_id,
    )
    if claims.get("tid", "").lower() != tenant_guid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unexpected Microsoft tenant")
    return claims


# Dev-only: issue a token for testing without a user DB
def issue_dev_token(tenant_id: str = "dev-tenant-001") -> str:
    return create_access_token(sub="dev-user", tenant_id=tenant_id)
