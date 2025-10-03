"""
Dashboard API endpoints for real-time statistics and activity feed
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from uuid import UUID
from app.core.dependencies import supabase, get_current_user, UserModel

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"], dependencies=[Depends(get_current_user)])


class ActivityItem(BaseModel):
    id: str
    type: str  # 'comment', 'issue', 'sprint', 'member', 'status'
    title: str
    description: str
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    timestamp: str
    link: Optional[str] = None


class DashboardStats(BaseModel):
    projects_count: int
    projects_trend: float
    issues_count: int
    issues_trend: float
    sprints_count: int
    sprints_trend: float
    teams_count: int
    teams_trend: float


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    workspace_id: Optional[str] = Query(None, description="Filter by specific workspace ID"),
    user: UserModel = Depends(get_current_user)
):
    """
    Get real dashboard statistics with week-over-week trends
    If workspace_id is provided, shows stats for that workspace only
    Otherwise shows stats across all user's workspaces
    """
    try:
        # Get current week date range
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)
        
        # Determine workspace IDs to filter by
        if workspace_id:
            # Verify user has access to this workspace
            workspace_check = supabase.table('workspace_members')\
                .select('workspace_id')\
                .eq('user_id', str(user.id))\
                .eq('workspace_id', workspace_id)\
                .execute()
            if not workspace_check.data:
                raise HTTPException(status_code=403, detail="Access denied to workspace")
            workspace_ids = [workspace_id]
        else:
            # Get all user's workspace IDs
            workspace_members = supabase.table('workspace_members')\
                .select('workspace_id')\
                .eq('user_id', str(user.id))\
                .execute()
            workspace_ids = [str(wm['workspace_id']) for wm in (workspace_members.data or [])]
        
        if not workspace_ids:
            # User has no workspaces - return zeros
            return DashboardStats(
                projects_count=0, projects_trend=0.0,
                issues_count=0, issues_trend=0.0,
                sprints_count=0, sprints_trend=0.0,
                teams_count=0, teams_trend=0.0
            )
        
        # Projects count (current) - filtered by user's workspaces
        projects_response = supabase.table('projects').select('id', count='exact').in_('workspace_id', workspace_ids).execute()  # type: ignore
        projects_count = projects_response.count or 0
        print(f"Dashboard stats - User's projects: {projects_count} (workspaces: {len(workspace_ids)})")
        
        # Projects count (last week) - filtered by user's workspaces
        projects_last_week = supabase.table('projects').select('id', count='exact').in_('workspace_id', workspace_ids).lt('created_at', week_ago.isoformat()).execute()  # type: ignore
        projects_last_week_count = projects_last_week.count or 0
        
        # Calculate projects trend
        if projects_last_week_count > 0:
            projects_trend = ((projects_count - projects_last_week_count) / projects_last_week_count) * 100
        else:
            projects_trend = 100.0 if projects_count > 0 else 0.0
        
        # Issues count (current) - filtered by user's workspaces
        issues_response = supabase.table('issues').select('id', count='exact').in_('workspace_id', workspace_ids).execute()  # type: ignore
        issues_count = issues_response.count or 0
        print(f"Dashboard stats - User's issues: {issues_count}")
        
        # Issues count (last week) - filtered by user's workspaces
        issues_last_week = supabase.table('issues').select('id', count='exact').in_('workspace_id', workspace_ids).lt('created_at', week_ago.isoformat()).execute()  # type: ignore
        issues_last_week_count = issues_last_week.count or 0
        
        # Calculate issues trend
        if issues_last_week_count > 0:
            issues_trend = ((issues_count - issues_last_week_count) / issues_last_week_count) * 100
        else:
            issues_trend = 100.0 if issues_count > 0 else 0.0
        
        # Sprints count (current) - filtered by user's workspaces  
        sprints_response = supabase.table('sprints').select('id', count='exact').in_('workspace_id', workspace_ids).execute()  # type: ignore
        sprints_count = sprints_response.count or 0
        
        # Sprints count (last week) - filtered by user's workspaces
        sprints_last_week = supabase.table('sprints').select('id', count='exact').in_('workspace_id', workspace_ids).lt('created_at', week_ago.isoformat()).execute()  # type: ignore
        sprints_last_week_count = sprints_last_week.count or 0
        
        # Calculate sprints trend
        if sprints_last_week_count > 0:
            sprints_trend = ((sprints_count - sprints_last_week_count) / sprints_last_week_count) * 100
        else:
            sprints_trend = 100.0 if sprints_count > 0 else 0.0
        
        # Teams count (current) - filtered by user's workspaces
        teams_response = supabase.table('teams').select('id', count='exact').in_('workspace_id', workspace_ids).execute()  # type: ignore
        teams_count = teams_response.count or 0
        
        # Teams count (last week) - filtered by user's workspaces
        teams_last_week = supabase.table('teams').select('id', count='exact').in_('workspace_id', workspace_ids).lt('created_at', week_ago.isoformat()).execute()  # type: ignore
        teams_last_week_count = teams_last_week.count or 0
        
        # Calculate teams trend
        if teams_last_week_count > 0:
            teams_trend = ((teams_count - teams_last_week_count) / teams_last_week_count) * 100
        else:
            teams_trend = 100.0 if teams_count > 0 else 0.0
        
        return DashboardStats(
            projects_count=projects_count,
            projects_trend=round(projects_trend, 1),
            issues_count=issues_count,
            issues_trend=round(issues_trend, 1),
            sprints_count=sprints_count,
            sprints_trend=round(sprints_trend, 1),
            teams_count=teams_count,
            teams_trend=round(teams_trend, 1)
        )
        
    except Exception as e:
        print(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/activity/feed", response_model=List[ActivityItem])
async def get_activity_feed(
    workspace_id: Optional[str] = Query(None, description="Filter by specific workspace ID"),
    limit: int = Query(20, ge=1, le=100),
    filter_type: Optional[str] = Query(None, regex="^(all|issues|comments|sprints)$"),
    user: UserModel = Depends(get_current_user)
):
    """
    Get recent activity feed for the dashboard
    If workspace_id provided, shows activity for that workspace only
    Otherwise shows activity across all user's workspaces
    """
    try:
        activities: List[ActivityItem] = []
        
        # Determine workspace IDs to filter by
        if workspace_id:
            # Verify user has access
            workspace_check = supabase.table('workspace_members')\
                .select('workspace_id')\
                .eq('user_id', str(user.id))\
                .eq('workspace_id', workspace_id)\
                .execute()
            if not workspace_check.data:
                raise HTTPException(status_code=403, detail="Access denied to workspace")
            workspace_ids = [workspace_id]
        else:
            # Get all user's workspace IDs
            workspace_members = supabase.table('workspace_members')\
                .select('workspace_id')\
                .eq('user_id', str(user.id))\
                .execute()
            workspace_ids = [str(wm['workspace_id']) for wm in (workspace_members.data or [])]
        
        if not workspace_ids:
            return []  # No workspaces, no activities
        
        # Get recent issues (created in last 7 days) - filtered by workspace
        issues_response = supabase.table('issues')\
            .select('id, issue_key, title, created_at, assignee_name, status, project_id')\
            .in_('workspace_id', workspace_ids)\
            .gte('created_at', (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        for issue in issues_response.data or []:
            activities.append(ActivityItem(
                id=str(issue['id']),
                type='issue',
                title='New issue created',
                description=f"{issue.get('issue_key', 'Issue')}: {issue['title']}",
                user_name=issue.get('assignee_name', 'Team Member'),
                timestamp=issue['created_at'],
                link=f"/dashboard/projects/{issue.get('project_id', '')}/issues" if issue.get('project_id') else '/dashboard/issues'
            ))
        
        # Get recent sprints - filtered by workspace
        sprints_response = supabase.table('sprints')\
            .select('id, name, state, created_at, end_date')\
            .in_('workspace_id', workspace_ids)\
            .gte('created_at', (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())\
            .order('created_at', desc=True)\
            .limit(10)\
            .execute()
        
        for sprint in sprints_response.data or []:
            activities.append(ActivityItem(
                id=str(sprint['id']),
                type='sprint',
                title='Sprint update',
                description=f"Sprint '{sprint['name']}' is {sprint.get('state', 'active')}",
                user_name='System',
                timestamp=sprint['created_at'],
                link='/dashboard/sprints'
            ))
        
        # Get recent team member additions - need to join through teams table for workspace
        # Note: team_members doesn't have workspace_id, need to filter teams first
        teams_in_workspace = supabase.table('teams')\
            .select('id')\
            .in_('workspace_id', workspace_ids)\
            .execute()
        team_ids = [str(t['id']) for t in (teams_in_workspace.data or [])]
        
        if team_ids:
            members_response = supabase.table('team_members')\
                .select('id, user_id, team_id, created_at')\
                .in_('team_id', team_ids)\
                .gte('created_at', (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())\
                .order('created_at', desc=True)\
                .limit(10)\
                .execute()
            
            for member in members_response.data or []:
                activities.append(ActivityItem(
                    id=str(member['id']),
                    type='member',
                    title='New team member',
                    description='A new member joined the team',
                    user_name='New Member',
                    timestamp=member['created_at'],
                    link='/dashboard/team/members'
                ))
        
        # Sort all activities by timestamp (most recent first)
        activities.sort(key=lambda x: x.timestamp, reverse=True)
        
        # Filter by type if specified
        if filter_type and filter_type != 'all':
            if filter_type == 'issues':
                activities = [a for a in activities if a.type in ['issue', 'status']]
            elif filter_type == 'comments':
                activities = [a for a in activities if a.type == 'comment']
            elif filter_type == 'sprints':
                activities = [a for a in activities if a.type == 'sprint']
        
        # Return limited results
        return activities[:limit]
        
    except Exception as e:
        print(f"Error fetching activity feed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/activity/recent")
async def get_recent_activity(
    user: UserModel = Depends(get_current_user),
    hours: int = Query(24, ge=1, le=168)  # Default 24 hours, max 7 days
):
    """
    Get summarized recent activity for the past N hours
    """
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        # Count new issues
        new_issues = supabase.table('issues').select('id', count='exact').gte('created_at', cutoff.isoformat()).execute()  # type: ignore
        
        # Count new sprints
        new_sprints = supabase.table('sprints').select('id', count='exact').gte('created_at', cutoff.isoformat()).execute()  # type: ignore
        
        # Count new team members
        new_members = supabase.table('team_members').select('id', count='exact').gte('created_at', cutoff.isoformat()).execute()  # type: ignore
        
        return {
            'period_hours': hours,
            'new_issues': new_issues.count or 0,
            'new_sprints': new_sprints.count or 0,
            'new_members': new_members.count or 0,
            'total_activities': (new_issues.count or 0) + (new_sprints.count or 0) + (new_members.count or 0)
        }
        
    except Exception as e:
        print(f"Error fetching recent activity summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))
