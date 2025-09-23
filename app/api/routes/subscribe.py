from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from app.core.dependencies import supabase
import logging

logger = logging.getLogger("cognisim_ai")

router = APIRouter(prefix="/api", tags=["Public"])


class SubscribeRequest(BaseModel):
    email: EmailStr
    source: str | None = "footer"


@router.post("/subscribe", status_code=201)
def subscribe(req: SubscribeRequest):
    try:
        # Upsert by email to avoid duplicates
        payload = {
            "email": req.email,
            "source": req.source or "footer",
        }
        res = supabase.table("subscribers").upsert(payload, on_conflict="email").execute()
        # Successful execute returns a response with .data; rely on exceptions otherwise

        return {"message": "Subscribed", "email": req.email}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscribe endpoint failed: {e}")
        raise HTTPException(status_code=500, detail="Subscription failed")
