from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4
from app.core.dependencies import supabase, get_current_user, UserModel

router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"], dependencies=[Depends(get_current_user)])

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)

class Workspace(BaseModel):
    id: UUID
    name: str

def _workspace_from_row(row: dict) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"]
    )

@router.get("", response_model=List[Workspace])
def list_workspaces(current_user: UserModel = Depends(get_current_user)):
    """List all workspaces for the current user"""
    # For now, we'll create a default workspace if none exists
    res = supabase.table("workspaces").select("id,name").execute()
    data = getattr(res, "data", []) or []
    
    if not data:
        # Create a default workspace
        default_workspace = {
            "id": str(uuid4()),
            "name": f"{current_user.email}'s Workspace"
        }
        ins = supabase.table("workspaces").insert(default_workspace).execute()
        ins_data = getattr(ins, "data", None)
        if ins_data:
            data = [ins_data[0]]
    
    return [_workspace_from_row(w) for w in data]

@router.post("", response_model=Workspace, status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceCreate, current_user: UserModel = Depends(get_current_user)):
    """Create a new workspace"""
    insert_payload = {
        "id": str(uuid4()),
        "name": body.name.strip()
    }
    
    ins = supabase.table("workspaces").insert(insert_payload).execute()
    data = getattr(ins, "data", None)
    if not data:
        raise HTTPException(status_code=500, detail="Failed to create workspace")
    
    return _workspace_from_row(data[0])

@router.get("/default", response_model=Workspace)
def get_default_workspace(current_user: UserModel = Depends(get_current_user)):
    """Get or create the default workspace for the user"""
    # Try to get existing workspace
    res = supabase.table("workspaces").select("id,name").limit(1).execute()
    data = getattr(res, "data", [])
    
    if data:
        return _workspace_from_row(data[0])
    
    # Create default workspace
    default_workspace = {
        "id": str(uuid4()),
        "name": f"{current_user.email}'s Workspace"
    }
    ins = supabase.table("workspaces").insert(default_workspace).execute()
    ins_data = getattr(ins, "data", None)
    if not ins_data:
        raise HTTPException(status_code=500, detail="Failed to create default workspace")
    
    return _workspace_from_row(ins_data[0])
