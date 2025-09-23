# feature_flags.py
# Service for managing and checking feature flags.

import logging
from fastapi import Depends, HTTPException, status
from supabase import Client

logger = logging.getLogger("cognisim_ai")

# A simple in-memory cache for the feature flags
_feature_flag_cache = {}

def load_feature_flags(supabase_client: Client):
    """
    Loads all feature flags from the database into the in-memory cache.
    This should be called on application startup.
    """
    global _feature_flag_cache
    try:
        response = supabase_client.table("feature_flags").select("name, is_enabled").execute()
        if response.data:
            _feature_flag_cache = {flag['name']: flag['is_enabled'] for flag in response.data}
            logger.info(f"Successfully loaded {_feature_flag_cache.__len__()} feature flags into cache.")
    except Exception as e:
        logger.error(f"Could not load feature flags from database: {e}")
        # In case of DB failure, default all flags to False to be safe
        _feature_flag_cache = {}

def feature_enabled(feature_name: str):
    """
    A dependency factory that creates a dependency to check if a feature is enabled.
    """
    async def _check_feature_flag():
        if not _feature_flag_cache.get(feature_name, False):
            # Log the attempt to access a disabled feature
            logger.warning(f"Access attempt to disabled feature: '{feature_name}'")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Feature '{feature_name}' is not enabled or does not exist."
            )
        return True
    return Depends(_check_feature_flag)
