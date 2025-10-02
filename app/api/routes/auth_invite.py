from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from app.core.dependencies import supabase, get_current_user, UserModel
from app.services.email_service import send_invitation_email
from uuid import uuid4
from datetime import datetime, timedelta
import os
import logging

logger = logging.getLogger("cognisim_ai")

router = APIRouter(prefix="/api/auth", tags=["Public Auth"]) 


class InviteRequest(BaseModel):
    email: EmailStr
    redirect: str | None = None


class TeamInviteRequest(BaseModel):
    """Team invitation with email notification."""
    email: EmailStr
    team_id: str | None = None
    workspace_id: str | None = None
    send_email: bool = True


class AcceptInviteRequest(BaseModel):
    """Accept an invitation by token."""
    token: str
    team_id: str | None = None
    workspace_id: str | None = None


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


@router.post("/invite/team")
def invite_to_team(req: TeamInviteRequest, current_user: UserModel = Depends(get_current_user)):
    """Send a team invitation with custom email notification.
    
    This endpoint creates an invitation record and optionally sends a branded email
    using the configured email service (Resend, Mailgun, or SendGrid).
    
    Args:
        req: Team invitation request with email and metadata
        current_user: Authenticated user sending the invitation
        
    Returns:
        dict with invitation status and email delivery info
    """
    try:
        # Generate invitation link (customize based on your frontend routing)
        base_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
        
        # Create invitation token/record in database (implement your logic)
        # For now, using a simple magic link approach
        invite_token = str(uuid4())
        
        # Store invitation in database
        invitation_data = {
            "id": invite_token,
            "email": req.email,
            "invited_by": str(current_user.id),
            "team_id": req.team_id,
            "workspace_id": req.workspace_id,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }
        
        try:
            supabase.table("invitations").insert(invitation_data).execute()
        except Exception as db_err:
            logger.warning(f"Failed to store invitation in DB: {db_err}")
            # Continue anyway - at minimum send the email
        
        # Build invitation link
        if req.team_id:
            invite_link = f"{base_url}/accept-invite?token={invite_token}&team={req.team_id}"
        elif req.workspace_id:
            invite_link = f"{base_url}/accept-invite?token={invite_token}&workspace={req.workspace_id}"
        else:
            invite_link = f"{base_url}/accept-invite?token={invite_token}"
        
        # Get inviter and workspace names
        inviter_name = current_user.email.split('@')[0].title()  # Fallback
        try:
            profile = supabase.table("user_profiles").select("full_name").eq("id", str(current_user.id)).maybe_single().execute()
            if profile and hasattr(profile, 'data') and profile.data and profile.data.get("full_name"):
                inviter_name = profile.data["full_name"]
        except Exception:
            pass
        
        workspace_name = "a workspace"
        if req.workspace_id:
            try:
                ws = supabase.table("workspaces").select("name").eq("id", req.workspace_id).maybe_single().execute()
                if ws and hasattr(ws, 'data') and ws.data and ws.data.get("name"):
                    workspace_name = ws.data["name"]
            except Exception:
                pass
        
        # Send email if requested
        email_result = None
        if req.send_email:
            try:
                email_result = send_invitation_email(
                    to_email=req.email,
                    invite_link=invite_link,
                    inviter_name=inviter_name,
                    workspace_name=workspace_name
                )
                logger.info(f"Invitation email sent to {req.email}: {email_result}")
            except Exception as email_err:
                logger.error(f"Failed to send invitation email: {email_err}")
                # Don't fail the entire request if email fails
                email_result = {"status": "failed", "error": str(email_err)}
        
        return {
            "message": "Invitation created",
            "token": invite_token,
            "invite_link": invite_link,
            "email_sent": req.send_email,
            "email_result": email_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Team invite endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create invitation: {str(e)}")


@router.post("/accept-invite")
def accept_invitation(req: AcceptInviteRequest, current_user: UserModel = Depends(get_current_user)):
    """Accept a team/workspace invitation.
    
    This endpoint processes invitation acceptance by:
    1. Validating the invitation token
    2. Checking expiration
    3. Adding user to the team/workspace
    4. Marking invitation as accepted
    
    Args:
        req: Invitation acceptance request with token
        current_user: Authenticated user accepting the invitation
        
    Returns:
        dict with acceptance status and team/workspace details
    """
    try:
        # Fetch invitation by token
        invite_res = (
            supabase.table("invitations")
            .select("*")
            .eq("token", req.token)
            .eq("status", "pending")
            .maybe_single()
            .execute()
        )
        
        invitation = getattr(invite_res, "data", None)
        if not invitation:
            raise HTTPException(status_code=404, detail="Invitation not found or already used")
        
        # Check expiration
        expires_at = invitation.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            now_utc = datetime.utcnow().replace(tzinfo=expires_dt.tzinfo)
            if now_utc > expires_dt:
                # Mark as expired
                supabase.table("invitations").update({"status": "expired"}).eq("token", req.token).execute()
                raise HTTPException(status_code=400, detail="Invitation has expired")
        
        # Verify email matches (optional - you might want to allow any authenticated user)
        user_profile = supabase.table("user_profiles").select("email").eq("user_id", str(current_user.id)).maybe_single().execute()
        user_data = getattr(user_profile, "data", None)
        if user_data and user_data.get("email") != invitation.get("email"):
            raise HTTPException(status_code=403, detail="This invitation was sent to a different email address")
        
        # Add user to team if team_id is present
        team_id = invitation.get("team_id") or req.team_id
        workspace_id = invitation.get("workspace_id") or req.workspace_id
        role = invitation.get("role", "viewer")
        
        result = {}
        
        if team_id:
            # Check if already a member
            existing_member = (
                supabase.table("team_members")
                .select("id")
                .eq("team_id", team_id)
                .eq("user_id", str(current_user.id))
                .maybe_single()
                .execute()
            )
            
            if not getattr(existing_member, "data", None):
                # Add to team
                supabase.table("team_members").insert({
                    "id": str(uuid4()),
                    "team_id": team_id,
                    "user_id": str(current_user.id),
                    "role": role,
                    "status": "active"
                }).execute()
                result["team_added"] = True
            else:
                result["team_added"] = False
                result["already_member"] = True
        
        if workspace_id:
            # Check if already a workspace member
            existing_ws_member = (
                supabase.table("workspace_members")
                .select("id")
                .eq("workspace_id", workspace_id)
                .eq("user_id", str(current_user.id))
                .maybe_single()
                .execute()
            )
            
            if not getattr(existing_ws_member, "data", None):
                # Add to workspace
                supabase.table("workspace_members").insert({
                    "id": str(uuid4()),
                    "workspace_id": workspace_id,
                    "user_id": str(current_user.id),
                    "role": role,
                    "status": "active"
                }).execute()
                result["workspace_added"] = True
            else:
                result["workspace_added"] = False
        
        # Mark invitation as accepted
        supabase.table("invitations").update({
            "status": "accepted",
            "accepted_at": datetime.utcnow().isoformat(),
            "accepted_by": str(current_user.id)
        }).eq("token", req.token).execute()
        
        return {
            "message": "Invitation accepted successfully",
            "team_id": team_id,
            "workspace_id": workspace_id,
            "role": role,
            **result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Accept invite failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to accept invitation: {str(e)}")

