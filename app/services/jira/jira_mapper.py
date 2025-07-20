# services/jira/jira_mapper.py
# Data mapping logic between Jira and CogniSim

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger("cognisim_ai")


class JiraFieldMapper:
    """
    Handles mapping between Jira issue fields and CogniSim item fields.
    """
    
    # Status mapping from Jira to our system
    STATUS_MAPPING = {
        'To Do': 'todo',
        'In Progress': 'in_progress',
        'Done': 'done',
        'Backlog': 'todo',
        'Selected for Development': 'todo',
        'In Review': 'in_progress',
        'Testing': 'in_progress',
        'Code Review': 'in_progress',
        'Ready for Testing': 'in_progress',
        'Closed': 'done',
        'Resolved': 'done',
        'Complete': 'done',
        'Cancelled': 'done',
        'Won\'t Do': 'done'
    }
    
    # Priority mapping from Jira to our system
    PRIORITY_MAPPING = {
        'Highest': 'critical',
        'High': 'high',
        'Medium': 'medium',
        'Low': 'low',
        'Lowest': 'low'
    }
    
    # Common story points field IDs in Jira
    STORY_POINTS_FIELDS = [
        'customfield_10016',  # Most common
        'customfield_10002',  # Alternative
        'customfield_10004',  # Alternative
        'story_points'        # Some configurations
    ]
    
    @staticmethod
    def jira_to_cognisim_item(
        jira_issue: Dict[str, Any], 
        project_id: str, 
        workspace_id: str
    ) -> Dict[str, Any]:
        """
        Map Jira issue to CogniSim item format.
        
        Args:
            jira_issue: Raw Jira issue data
            project_id: Target CogniSim project ID
            workspace_id: Target workspace ID
            
        Returns:
            Dict containing mapped item data
        """
        try:
            fields = jira_issue.get('fields', {})
            issue_key = jira_issue.get('key', '')
            
            # Map basic fields
            mapped_item = {
                'id': str(uuid4()),  # Generate new UUID for the item
                'title': fields.get('summary', ''),
                'description': JiraFieldMapper._clean_description(fields.get('description')),
                'status': JiraFieldMapper._map_status(fields.get('status', {}).get('name')),
                'priority': JiraFieldMapper._map_priority(fields.get('priority', {}).get('name', 'Medium')),
                'story_points': JiraFieldMapper._extract_story_points(fields),
                'assignee_id': JiraFieldMapper._get_user_email(fields.get('assignee')),
                'reporter_id': JiraFieldMapper._get_user_email(fields.get('reporter')),
                'project_id': project_id,
                'workspace_id': workspace_id,
                'item_type': JiraFieldMapper._map_issue_type(fields.get('issuetype', {}).get('name', 'Task')),
                'labels': JiraFieldMapper._extract_labels(fields.get('labels', [])),
                'due_date': JiraFieldMapper._parse_date(fields.get('duedate')),
                'created_at': JiraFieldMapper._parse_datetime(fields.get('created')),
                'updated_at': JiraFieldMapper._parse_datetime(fields.get('updated')),
                # Store original Jira data for reference
                'jira_metadata': {
                    'issue_key': issue_key,
                    'issue_id': jira_issue.get('id'),
                    'issue_type': fields.get('issuetype', {}).get('name'),
                    'original_status': fields.get('status', {}).get('name'),
                    'original_priority': fields.get('priority', {}).get('name')
                }
            }
            
            logger.info(f"Successfully mapped Jira issue {issue_key} to CogniSim item")
            return mapped_item
            
        except Exception as e:
            logger.error(f"Failed to map Jira issue: {str(e)}")
            raise ValueError(f"Issue mapping failed: {str(e)}")
    
    @staticmethod
    def _map_status(jira_status: Optional[str]) -> str:
        """Map Jira status to our status enum."""
        if not jira_status:
            return 'todo'
        return JiraFieldMapper.STATUS_MAPPING.get(jira_status, 'todo')
    
    @staticmethod
    def _map_priority(jira_priority: str) -> str:
        """Map Jira priority to our priority enum."""
        return JiraFieldMapper.PRIORITY_MAPPING.get(jira_priority, 'medium')
    
    @staticmethod
    def _map_issue_type(jira_issue_type: str) -> str:
        """Map Jira issue type to our item type."""
        issue_type_mapping = {
            'Story': 'story',
            'Task': 'task',
            'Bug': 'bug',
            'Epic': 'epic',
            'Sub-task': 'subtask',
            'Improvement': 'task',
            'New Feature': 'story'
        }
        return issue_type_mapping.get(jira_issue_type, 'task')
    
    @staticmethod
    def _extract_story_points(fields: Dict[str, Any]) -> Optional[int]:
        """Extract story points from Jira custom fields."""
        for field_name in JiraFieldMapper.STORY_POINTS_FIELDS:
            story_points = fields.get(field_name)
            if story_points is not None:
                try:
                    return int(float(story_points))
                except (ValueError, TypeError):
                    continue
        return None
    
    @staticmethod
    def _get_user_email(jira_user: Optional[Dict]) -> Optional[str]:
        """Extract user email from Jira user object."""
        if not jira_user:
            return None
        return jira_user.get('emailAddress') or jira_user.get('name')
    
    @staticmethod
    def _extract_labels(jira_labels: List[str]) -> List[str]:
        """Extract and clean labels from Jira."""
        if not jira_labels:
            return []
        return [label.strip() for label in jira_labels if label.strip()]
    
    @staticmethod
    def _clean_description(description: Optional[str]) -> str:
        """Clean and format Jira description."""
        if not description:
            return ""
        
        # Remove Jira markup if present
        # This is a basic cleanup - could be enhanced with proper Jira markup parsing
        cleaned = description.replace('{code}', '```').replace('{code}', '```')
        cleaned = cleaned.replace('{quote}', '> ').replace('{quote}', '')
        
        return cleaned.strip()
    
    @staticmethod
    def _parse_date(date_string: Optional[str]) -> Optional[str]:
        """Parse Jira date format to ISO format."""
        if not date_string:
            return None
        try:
            # Jira date format: YYYY-MM-DD
            return date_string
        except:
            return None
    
    @staticmethod
    def _parse_datetime(datetime_string: Optional[str]) -> Optional[str]:
        """Parse Jira datetime format to ISO format."""
        if not datetime_string:
            return None
        try:
            # Jira datetime format: 2023-07-19T10:30:00.000+0000
            # Parse and convert to ISO format
            if 'T' in datetime_string:
                return datetime_string.split('.')[0] + 'Z'
            return datetime_string
        except:
            return None
    
    @staticmethod
    def create_integration_mapping(
        item_id: str,
        jira_issue_key: str,
        jira_issue_id: str,
        jira_url: str
    ) -> Dict[str, Any]:
        """
        Create integration mapping record.
        
        Args:
            item_id: CogniSim item ID
            jira_issue_key: Jira issue key (e.g., 'PROJ-123')
            jira_issue_id: Jira issue ID
            jira_url: Base Jira URL
            
        Returns:
            Integration mapping data
        """
        return {
            'id': str(uuid4()),
            'item_id': item_id,
            'external_system': 'jira',
            'external_item_id': jira_issue_key,
            'external_url': f"{jira_url.rstrip('/')}/browse/{jira_issue_key}",
            'last_synced_at': datetime.utcnow().isoformat(),
            'created_at': datetime.utcnow().isoformat()
        }
