# 🚀 Quick Setup - Jira OAuth Integration

Your frontend is **already running** on http://localhost:8080 (Vite). Just complete these steps:

---

## Step 1: Create Atlassian OAuth App (5 min)

### 1.1 Create App
1. Go to: https://developer.atlassian.com/console/myapps/
2. Click **Create** → **OAuth 2.0 integration**
3. Name: "CogniSim AI"
4. Click **Create**

### 1.2 Add Permissions
1. Go to **Permissions** tab → **Jira API** → **Add**
2. Select these scopes:
   - ✅ `read:jira-user`
   - ✅ `read:jira-work`
   - ✅ `write:jira-work`
   - ✅ `offline_access`
3. Click **Save**

### 1.3 Set Callback URL
1. Go to **Authorization** tab
2. Add: `http://localhost:8000/api/integrations/jira/oauth/callback`
   - Note: Backend API runs on port 8000, frontend on 8080
3. Click **Save**

### 1.4 Get Credentials
1. Go to **Settings** tab
2. **Copy** your Client ID
3. Click **Generate a secret** → **Copy** the secret

---

## Step 2: Configure Backend Environment (2 min)

Edit `cognisim_ai_backend/.env` (create if doesn't exist):

```env
# Jira OAuth Configuration
JIRA_OAUTH_CLIENT_ID=paste_your_client_id_here
JIRA_OAUTH_CLIENT_SECRET=paste_your_secret_here
JIRA_OAUTH_REDIRECT_URI=http://localhost:8000/api/integrations/jira/oauth/callback
FRONTEND_URL=http://localhost:8080

# Your existing Supabase config should already be here
```

---

## Step 3: Run Database Migrations (2 min)

### Option A: Supabase Dashboard (Recommended)
1. Open Supabase → **SQL Editor**
2. Copy & run: `cognisim_ai_backend/migrations/create_oauth_states_table.sql`
3. Copy & run: `cognisim_ai_backend/migrations/add_oauth_fields_to_integration_credentials.sql`

### Option B: Command Line
```powershell
# From cognisim_ai_backend folder
psql -h your-db-host -U your-user -d your-db -f migrations/create_oauth_states_table.sql
psql -h your-db-host -U your-user -d your-db -f migrations/add_oauth_fields_to_integration_credentials.sql
```

---

## Step 4: Install Dependencies (1 min)

```powershell
cd cognisim_ai_backend
pip install httpx
```

Add to `requirements.txt`:
```
httpx>=0.24.0
```

---

## Step 5: Start Backend (1 min)

```powershell
cd cognisim_ai_backend
python run_server.py
```

Backend should start on port 8000 (same as frontend).

---

## Step 6: Test It! 🎉

1. **Open:** http://localhost:8080/integrations
2. **Click:** "Connect with Jira" button
3. **Redirected to:** Atlassian login page
4. **Login** and click **Accept**
5. **Redirected back** with success message
6. **Done!** ✅

---

## ✅ Verification Checklist

After completing all steps, verify:

- [ ] Backend running without errors
- [ ] No errors in backend logs
- [ ] Frontend shows "Connect with Jira" button
- [ ] Clicking button redirects to `auth.atlassian.com`
- [ ] After accepting, redirected to `/integrations?connected=true`
- [ ] Success notification appears
- [ ] Status shows green "Connected" badge
- [ ] "Disconnect" button appears
- [ ] Can click "Sync now" successfully

---

## 🐛 Troubleshooting

### "Invalid OAuth state"
- **Cause:** Flow took >10 minutes or browser cleared data
- **Fix:** Click "Connect with Jira" again

### "No accessible Jira sites found"
- **Cause:** Account has no Jira access
- **Fix:** Ensure you have access to at least one Jira site

### Button doesn't redirect
- **Check:** Browser console for errors
- **Check:** Backend is running on port 8000
- **Check:** `.env` file has correct values
- **Check:** No extra spaces/quotes in `.env`

### "Token exchange failed"
- **Check:** Client ID and Secret are correct
- **Check:** Callback URL matches exactly (use 127.0.0.1)
- **Check:** All 4 OAuth scopes are added in Atlassian

### Backend won't start
- **Check:** Port 8000 not already in use
- **Check:** All environment variables set
- **Check:** `httpx` is installed

---

## 📊 What Changed?

**Before (Manual):**
```
1. Enter Jira URL
2. Enter email  
3. Generate API token
4. Paste token
5. Click Connect
```

**After (OAuth):**
```
1. Click "Connect with Jira"
2. Click "Accept"
3. Done! ✨
```

---

## 📚 Need More Help?

- **Detailed Guide:** `docs/JIRA_OAUTH_SETUP.md`
- **Flow Diagram:** `docs/OAUTH_FLOW_DIAGRAM.md`
- **Backend Logs:** Check terminal running `run_server.py`

---

**Setup time:** ~10 minutes total

**Your frontend is already configured and running!** Just set up the backend and you're good to go. 🚀
