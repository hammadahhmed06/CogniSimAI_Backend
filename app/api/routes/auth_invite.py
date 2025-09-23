from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.core.dependencies import supabase
import logging

logger = logging.getLogger("cognisim_ai")

router = APIRouter(prefix="/api/auth", tags=["Public Auth"]) 


class InviteRequest(BaseModel):
    email: EmailStr
    redirect: str | None = None


@router.post("/invite")
def invite_user(req: InviteRequest):
    """Send an invite email (admin) or fall back to magic link.

    - Uses Supabase service role on the backend to access admin APIs.
    - If admin.invite_user_by_email is not available, falls back to sign_in_with_otp.
    """
    redirect_to = req.redirect
    try:
        # Prefer Admin Invite (requires service role)
        admin = getattr(getattr(supabase, 'auth', None), 'admin', None)
        if admin is not None:
            invite_fn = getattr(admin, 'invite_user_by_email', None)
            if callable(invite_fn):
                try:
                    if redirect_to:
                        invite_fn(req.email, options={"redirect_to": redirect_to})
                    else:
                        invite_fn(req.email)
                    return {"message": "Invite sent", "mode": "admin_invite"}
                except Exception as e:
                    logger.warning(f"Admin invite failed, falling back to OTP: {e}")
        # Fallback to OTP magic link
        auth = getattr(supabase, 'auth', None)
        if auth is not None:
            otp_fn = getattr(auth, 'sign_in_with_otp', None)
            if callable(otp_fn):
                if redirect_to:
                    otp_fn({"email": req.email, "options": {"email_redirect_to": redirect_to}})
                else:
                    otp_fn({"email": req.email})
                return {"message": "Magic link sent", "mode": "otp_fallback"}
        raise HTTPException(status_code=500, detail="Auth provider not available")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Invite endpoint failed: {e}")
        raise HTTPException(status_code=500, detail="Invite failed")
