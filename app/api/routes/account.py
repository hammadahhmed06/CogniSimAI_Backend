from fastapi import APIRouter, Depends, HTTPException, status
from app.core.dependencies import get_current_user, UserModel, supabase, bearer_scheme
from fastapi.security import HTTPAuthorizationCredentials
from datetime import datetime, timezone
import logging

logger = logging.getLogger("cognisim_ai")

router = APIRouter(prefix="/api/account", tags=["Account"], dependencies=[])

@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/")
def delete_account(current_user: UserModel = Depends(get_current_user), credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    try:
        # Enforce recent reauthentication within last 5 minutes using last_sign_in_at
        try:
            token = credentials.credentials
            user_resp = supabase.auth.get_user(token)
            sb_user = getattr(user_resp, "user", None)
            last_sign_in_at = getattr(sb_user, "last_sign_in_at", None)
            if not last_sign_in_at:
                raise ValueError("missing last_sign_in_at")
            # Parse ISO time
            try:
                last_dt = datetime.fromisoformat(str(last_sign_in_at).replace("Z", "+00:00"))
            except Exception:
                last_dt = None
            if not last_dt:
                raise ValueError("invalid last_sign_in_at")
            now = datetime.now(timezone.utc)
            # 5 minutes freshness window
            if (now - last_dt).total_seconds() > 300:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Recent reauthentication required")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Could not confirm recent reauth: {e}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Recent reauthentication required")

        uid = str(current_user.id)
        # Best-effort purge user-owned rows first
        try:
            supabase.table("user_profiles").delete().eq("id", uid).execute()
        except Exception as e:
            logger.warning(f"Delete user_profiles failed for {uid}: {e}")
        try:
            supabase.table("user_settings").delete().eq("user_id", uid).execute()
        except Exception as e:
            logger.warning(f"Delete user_settings failed for {uid}: {e}")
        # Delete auth user via admin API (service role)
        admin = getattr(getattr(supabase, 'auth', None), 'admin', None)
        if not admin:
            raise HTTPException(status_code=500, detail="Auth admin not available")
        delete_fn = getattr(admin, 'delete_user', None)
        if not callable(delete_fn):
            raise HTTPException(status_code=500, detail="Delete not supported")
        delete_fn(uid)
        return
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Account deletion failed for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Account deletion failed")
