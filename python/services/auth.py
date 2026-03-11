"""JWT authentication dependency for FastAPI endpoints.

Validates Supabase JWTs (HS256) from the Authorization: Bearer header.
"""

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import SUPABASE_JWT_SECRET

_bearer = HTTPBearer()


def verify_jwt(credentials: HTTPAuthorizationCredentials = Security(_bearer)) -> dict:
    """
    FastAPI dependency. Validates the Supabase JWT from the Authorization header.

    Returns the decoded token payload, which includes:
      - sub:           user UUID (use this as the verified user ID)
      - email:         user email
      - role:          "authenticated" for regular users
      - app_metadata:  server-side metadata (e.g. is_admin)

    Raises HTTP 401 if the token is missing or invalid.
    Raises HTTP 403 if the token role is not "authenticated" or "service_role".
    """
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_JWT_SECRET is not configured on the server",
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase doesn't always set aud
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {e}")

    if payload.get("role") not in ("authenticated", "service_role"):
        raise HTTPException(status_code=403, detail="Token has insufficient role")

    return payload
