# Jira OAuth Integration Setup Guide

> **⚡ Quick Setup:** For a streamlined setup guide, see [`QUICK_SETUP.md`](../QUICK_SETUP.md) in the root folder.
> This document provides comprehensive details for production and advanced configurations.

This guide explains how to set up OAuth-based Jira integration for CogniSim AI.

## Overview

The application now supports **OAuth 2.0 (3-legged OAuth)** for Jira integration instead of manual API token entry. This provides:

- ✅ Better user experience - just click "Connect with Jira"
- ✅ Automatic token management and refresh
- ✅ More secure authentication flow
- ✅ Access to multiple Jira sites
- ✅ Easy disconnect/revoke access

## Setup Instructions

### 1. Create an Atlassian OAuth 2.0 App

1. Go to [Atlassian Developer Console](https://developer.atlassian.com/console/myapps/)
2. Click **Create** → **OAuth 2.0 integration**
3. Fill in the app details:
   - **App name**: CogniSim AI (or your preferred name)
   - **Description**: Project management integration for CogniSim AI
4. Click **Create**

### 2. Configure OAuth Settings

1. In your newly created app, go to **Permissions** tab
2. Add the following scopes:
   - `read:jira-user` - Read user information
   - `read:jira-work` - Read Jira data
   - `write:jira-work` - Create and update Jira issues
   - `offline_access` - Get refresh tokens for long-term access

3. Go to **Authorization** tab
4. Add the callback URL:
   - For local development: `http://localhost:8000/api/integrations/jira/oauth/callback`
   - **Note:** Backend API runs on port 8000, frontend on port 8080
   - For production: `https://your-api-domain.com/api/integrations/jira/oauth/callback`

5. Click **Save changes**

### 3. Get OAuth Credentials

1. In the **Settings** tab, you'll find:
   - **Client ID** - Copy this value
   - **Secret** - Click "Generate a secret" and copy the value

### 4. Configure Backend Environment

Update your `.env` file in the `cognisim_ai_backend` folder:

```env
# Jira OAuth Configuration
JIRA_OAUTH_CLIENT_ID=your_client_id_here
JIRA_OAUTH_CLIENT_SECRET=your_client_secret_here
JIRA_OAUTH_REDIRECT_URI=http://localhost:8000/api/integrations/jira/oauth/callback
FRONTEND_URL=http://localhost:8080
```

**Note:** Your frontend is on port 8080, backend API on port 8000.

**For Production:**
```env
JIRA_OAUTH_REDIRECT_URI=https://your-api-domain.com/api/integrations/jira/oauth/callback
FRONTEND_URL=https://your-frontend-domain.com
```

### 5. Run Database Migrations

Apply the database migrations to add required tables:

```bash
cd cognisim_ai_backend

# Run the OAuth states table migration
psql -h your-db-host -U your-db-user -d your-db-name -f migrations/create_oauth_states_table.sql

# Run the integration credentials update migration
psql -h your-db-host -U your-db-user -d your-db-name -f migrations/add_oauth_fields_to_integration_credentials.sql
```

**For Supabase:**
1. Go to your Supabase project dashboard
2. Navigate to **SQL Editor**
3. Copy and paste the contents of each migration file
4. Run them in order

### 6. Restart the Backend

```bash
cd cognisim_ai_backend
python run_server.py
```

## How It Works

### User Flow

1. **User clicks "Connect with Jira"** on the Integrations page
2. User is redirected to Atlassian's authorization page
3. User logs in to their Atlassian account (if not already logged in)
4. User grants permissions to the app
5. User is redirected back to the app with an authorization code
6. Backend exchanges the code for access and refresh tokens
7. Tokens are encrypted and stored securely
8. User is redirected back to the Integrations page with success message

### Disconnection Flow

1. **User clicks "Disconnect"** button
2. Confirmation dialog appears
3. Upon confirmation, stored credentials are deleted from the database
4. User can reconnect anytime by clicking "Connect with Jira" again

## API Endpoints

### `GET /api/integrations/jira/oauth/init`
Initiates the OAuth flow and returns the authorization URL.

**Response:**
```json
{
  "authorization_url": "https://auth.atlassian.com/authorize?...",
  "state": "random_state_token"
}
```

### `GET /api/integrations/jira/oauth/callback`
Handles the OAuth callback from Jira and exchanges the code for tokens.

**Query Parameters:**
- `code` - Authorization code from Jira
- `state` - State token for CSRF protection

**Redirects to:**
- Success: `{FRONTEND_URL}/integrations?connected=true`
- Error: `{FRONTEND_URL}/integrations?error={error_code}`

### `POST /api/integrations/jira/disconnect`
Disconnects the Jira integration and removes stored credentials.

**Response:**
```json
{
  "success": true,
  "message": "Jira integration disconnected successfully"
}
```

## Security Features

1. **CSRF Protection**: State parameter validates the OAuth flow
2. **Token Encryption**: Access and refresh tokens are encrypted before storage
3. **Expiration**: OAuth states expire after 10 minutes
4. **Secure Storage**: Credentials stored in database with encryption
5. **HTTPS Required**: Production should use HTTPS for all OAuth flows

## Troubleshooting

### "Invalid OAuth state" Error
- The OAuth flow took too long (>10 minutes)
- Browser cleared cookies/storage during flow
- **Solution**: Try connecting again

### "No accessible Jira sites found" Error
- User doesn't have access to any Jira sites
- **Solution**: Ensure user has access to at least one Jira site

### "Token exchange failed" Error
- OAuth credentials are incorrect
- Redirect URI doesn't match
- **Solution**: Verify `.env` configuration and Atlassian app settings

### Connection shows but can't fetch data
- Access token may have expired
- **Solution**: Implement token refresh (see next section)

## Token Refresh (Advanced)

OAuth access tokens expire after a period. The refresh token can be used to get new access tokens without user interaction. To implement:

1. Check token expiration before API calls
2. Use the `jira_refresh_token_encrypted` to get new access token
3. Update stored credentials with new access token

Example refresh endpoint (to be implemented):
```python
@router.post("/jira/oauth/refresh")
async def refresh_jira_token(workspace_id: str):
    # Get stored refresh token
    # Exchange for new access token
    # Update credentials in database
    pass
```

## Migration from Manual Tokens

Existing manual token connections will continue to work. Users can:
1. Keep using manual tokens (keep the old `/jira/connect` endpoint)
2. Disconnect and reconnect using OAuth for better experience

## Support

For issues or questions:
- Check logs in `cognisim_ai_backend/logs/`
- Verify environment variables are set correctly
- Ensure database migrations are applied
- Check Atlassian Developer Console for app status

## References

- [Atlassian OAuth 2.0 Documentation](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/)
- [OAuth 2.0 RFC](https://tools.ietf.org/html/rfc6749)
- [Jira REST API Documentation](https://developer.atlassian.com/cloud/jira/platform/rest/v3/)
