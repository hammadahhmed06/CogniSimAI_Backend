"""Email service for sending invitations and transactional emails.

Uses Resend API (free tier: 3,000 emails/month)

IMPORTANT: Resend free tier limitations:
- Testing mode: Can only send to your own verified email
- Production: Must verify a domain at resend.com/domains
"""

import os
import logging
from typing import Optional, Literal, Any
from pydantic import BaseModel, EmailStr

logger = logging.getLogger("cognisim_ai")

EmailProvider = Literal["resend", "mailgun", "sendgrid"]


class EmailConfig(BaseModel):
    """Email service configuration."""
    provider: EmailProvider = "resend"
    api_key: str
    from_email: EmailStr
    from_name: str = "CogniSim AI"


class EmailMessage(BaseModel):
    """Email message structure."""
    to: EmailStr
    subject: str
    html: str
    text: Optional[str] = None


def _send_via_resend(config: EmailConfig, message: EmailMessage) -> dict[str, Any]:
    """Send email via Resend API (3,000/month free)."""
    try:
        import resend
    except ImportError:
        raise ImportError("resend package not installed. Run: pip install resend")
    
    resend.api_key = config.api_key
    
    # Resend expects a params dict as a single argument
    params: dict[str, Any] = {
        "from": f"{config.from_name} <{config.from_email}>",
        "to": [message.to],
        "subject": message.subject,
        "html": message.html,
    }
    
    if message.text:
        params["text"] = message.text
    
    response = resend.Emails.send(params)  # type: ignore
    logger.info(f"Email sent via Resend to {message.to}: {response}")
    return {"provider": "resend", "id": response.get("id"), "status": "sent"}





def send_email(message: EmailMessage, provider: Optional[str] = None) -> dict[str, Any]:
    """Send an email using configured provider with automatic fallback.
    
    Args:
        message: Email message to send
        provider: Specific provider to use (defaults to env config)
        
    Returns:
        dict with provider, id/status, and status
        
    Raises:
        ValueError: If no provider is configured
        Exception: If all providers fail
    """
    # Load configuration from environment
    configured_provider = provider or os.getenv("EMAIL_PROVIDER", "resend")
    api_key = os.getenv("EMAIL_API_KEY") or os.getenv(f"{configured_provider.upper()}_API_KEY")
    from_email = os.getenv("EMAIL_FROM", "noreply@cognisim.ai")
    from_name = os.getenv("EMAIL_FROM_NAME", "CogniSim AI")
    
    if not api_key:
        raise ValueError(
            f"Email API key not configured. Set EMAIL_API_KEY or {configured_provider.upper()}_API_KEY"
        )
    
    # Validate provider
    valid_providers: list[EmailProvider] = ["resend", "mailgun", "sendgrid"]
    if configured_provider not in valid_providers:
        configured_provider = "resend"  # Default fallback
    
    config = EmailConfig(
        provider=configured_provider,  # type: ignore
        api_key=api_key,
        from_email=from_email,
        from_name=from_name
    )
    
    # Send via Resend only
    try:
        return _send_via_resend(config, message)
    except Exception as e:
        logger.error(f"Failed to send email via Resend: {e}")
        raise Exception(f"Email sending failed: {str(e)}")


def send_invitation_email(
    to_email: str,
    invite_link: str,
    inviter_name: Optional[str] = None,
    workspace_name: Optional[str] = None
) -> dict[str, Any]:
    """Send a team/workspace invitation email.
    
    Args:
        to_email: Recipient email address
        invite_link: Full invitation URL with token
        inviter_name: Name of person sending invite (optional)
        workspace_name: Name of workspace/team (optional)
        
    Returns:
        dict with send status
    """
    inviter = inviter_name or "A team member"
    workspace = workspace_name or "their workspace"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>You're Invited to CogniSim AI</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5;">
        <table role="presentation" style="width: 100%; border-collapse: collapse;">
            <tr>
                <td align="center" style="padding: 40px 0;">
                    <table role="presentation" style="width: 600px; max-width: 100%; border-collapse: collapse; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                        <!-- Header -->
                        <tr>
                            <td style="padding: 40px 40px 20px; text-align: center; background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%); border-radius: 8px 8px 0 0;">
                                <h1 style="margin: 0; color: #ffffff; font-size: 28px; font-weight: 600;">
                                    CogniSim AI
                                </h1>
                            </td>
                        </tr>
                        
                        <!-- Content -->
                        <tr>
                            <td style="padding: 40px;">
                                <h2 style="margin: 0 0 16px; color: #1e293b; font-size: 24px; font-weight: 600;">
                                    You've been invited!
                                </h2>
                                <p style="margin: 0 0 24px; color: #475569; font-size: 16px; line-height: 24px;">
                                    {inviter} has invited you to join {workspace} on CogniSim AI.
                                </p>
                                <p style="margin: 0 0 32px; color: #475569; font-size: 16px; line-height: 24px;">
                                    CogniSim AI helps teams plan sprints, manage backlogs, and leverage AI-powered story generation to ship faster.
                                </p>
                                
                                <!-- CTA Button -->
                                <table role="presentation" style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td align="center">
                                            <a href="{invite_link}" style="display: inline-block; padding: 14px 32px; background-color: #2563eb; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">
                                                Accept Invitation
                                            </a>
                                        </td>
                                    </tr>
                                </table>
                                
                                <p style="margin: 32px 0 0; color: #94a3b8; font-size: 14px; line-height: 20px;">
                                    Or copy and paste this link into your browser:
                                </p>
                                <p style="margin: 8px 0 0; color: #64748b; font-size: 14px; line-height: 20px; word-break: break-all;">
                                    {invite_link}
                                </p>
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="padding: 32px 40px; border-top: 1px solid #e2e8f0; background-color: #f8fafc; border-radius: 0 0 8px 8px;">
                                <p style="margin: 0; color: #64748b; font-size: 14px; line-height: 20px; text-align: center;">
                                    This invitation was sent to {to_email}. If you weren't expecting this, you can safely ignore this email.
                                </p>
                                <p style="margin: 16px 0 0; color: #94a3b8; font-size: 12px; line-height: 18px; text-align: center;">
                                    © 2025 CogniSim AI. All rights reserved.
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    
    text = f"""
You've been invited to CogniSim AI!

{inviter} has invited you to join {workspace} on CogniSim AI.

CogniSim AI helps teams plan sprints, manage backlogs, and leverage AI-powered story generation to ship faster.

Accept your invitation:
{invite_link}

This invitation was sent to {to_email}. If you weren't expecting this, you can safely ignore this email.

© 2025 CogniSim AI. All rights reserved.
    """
    
    message = EmailMessage(
        to=to_email,
        subject=f"You're invited to join {workspace} on CogniSim AI",
        html=html,
        text=text
    )
    
    return send_email(message)
