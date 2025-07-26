#!/usr/bin/env python3
"""
Data Migration Script for Jira API Token Encryption

This script migrates existing Jira API tokens from simple Base64 encoding 
to AES-256-GCM encryption. It's designed to be run during deployment to 
upgrade existing credentials without downtime.

Usage:
    python migrate_credentials.py [--dry-run] [--batch-size N]

Options:
    --dry-run       Preview changes without applying them
    --batch-size    Number of credentials to process at once (default: 10)
    --force         Force migration even if some credentials appear encrypted
    --verbose       Enable detailed logging
"""

import sys
import os
import argparse
import logging
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

# Add the app directory to the path
sys.path.append(str(Path(__file__).parent))

from app.core.config import settings
from app.services.encryption.token_encryption import get_token_encryption_service
from app.services.encryption.simple_credential_store import simple_credential_store
from supabase import create_client, Client


class CredentialMigrationScript:
    """
    Script to migrate Jira API credentials from simple encoding to AES encryption.
    """
    
    def __init__(self, supabase_client: Client, dry_run: bool = False, batch_size: int = 10):
        """
        Initialize the migration script.
        
        Args:
            supabase_client: Supabase client for database operations
            dry_run: If True, preview changes without applying them
            batch_size: Number of credentials to process at once
        """
        self.supabase = supabase_client
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.encryption_service = get_token_encryption_service()
        
        # Statistics
        self.stats = {
            'total_found': 0,
            'already_encrypted': 0,
            'migrated': 0,
            'failed': 0,
            'errors': []
        }
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(f'migration_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    async def get_all_credentials(self) -> List[Dict[str, Any]]:
        """
        Retrieve all Jira credentials from the database.
        
        Returns:
            List of credential records
        """
        try:
            result = self.supabase.table("integration_credentials")\
                .select("*")\
                .eq("integration_type", "jira")\
                .eq("is_active", True)\
                .execute()
            
            credentials = result.data or []
            self.stats['total_found'] = len(credentials)
            
            self.logger.info(f"Found {len(credentials)} Jira credentials to analyze")
            return credentials
            
        except Exception as e:
            self.logger.error(f"Failed to retrieve credentials: {str(e)}")
            raise
    
    def analyze_credential(self, credential: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze a single credential to determine migration status.
        
        Args:
            credential: The credential record
            
        Returns:
            Analysis result with migration plan
        """
        credential_id = credential.get('id', 'unknown')
        workspace_id = credential.get('workspace_id', 'unknown')
        encrypted_token = credential.get('jira_api_token_encrypted', '')
        
        analysis = {
            'id': credential_id,
            'workspace_id': workspace_id,
            'needs_migration': False,
            'is_encrypted': False,
            'can_decode_old': False,
            'error': None,
            'plaintext_token': None
        }
        
        try:
            # Check if already encrypted
            if self.encryption_service.is_encrypted(encrypted_token):
                analysis['is_encrypted'] = True
                self.logger.info(f"Credential {credential_id} (workspace: {workspace_id}) already encrypted")
                return analysis
            
            # Try to decode with old system
            try:
                plaintext_token = simple_credential_store.decode_credential(encrypted_token)
                analysis['can_decode_old'] = True
                analysis['needs_migration'] = True
                analysis['plaintext_token'] = plaintext_token
                self.logger.info(f"Credential {credential_id} (workspace: {workspace_id}) can be migrated")
            except Exception as decode_error:
                # Might be plaintext or corrupted
                self.logger.warning(f"Could not decode credential {credential_id} with old system: {decode_error}")
                # Try to use as plaintext (fallback)
                if encrypted_token and len(encrypted_token) > 0:
                    analysis['needs_migration'] = True
                    analysis['plaintext_token'] = encrypted_token
                    self.logger.info(f"Treating credential {credential_id} as plaintext for migration")
                else:
                    analysis['error'] = "Empty or invalid token"
        
        except Exception as e:
            analysis['error'] = str(e)
            self.logger.error(f"Failed to analyze credential {credential_id}: {e}")
        
        return analysis
    
    async def migrate_credential(self, credential: Dict[str, Any], analysis: Dict[str, Any]) -> bool:
        """
        Migrate a single credential to encrypted format.
        
        Args:
            credential: Original credential record
            analysis: Analysis result from analyze_credential
            
        Returns:
            True if migration successful, False otherwise
        """
        credential_id = analysis['id']
        workspace_id = analysis['workspace_id']
        
        try:
            if not analysis['needs_migration']:
                self.logger.info(f"Skipping credential {credential_id} - no migration needed")
                return True
            
            if not analysis['plaintext_token']:
                self.logger.error(f"No plaintext token available for credential {credential_id}")
                return False
            
            # Encrypt with new system
            new_encrypted_token = self.encryption_service.encrypt(analysis['plaintext_token'])
            
            if self.dry_run:
                self.logger.info(f"[DRY RUN] Would update credential {credential_id} with new encryption")
                return True
            
            # Update database
            update_data = {
                'jira_api_token_encrypted': new_encrypted_token,
                'updated_at': datetime.utcnow().isoformat()
            }
            
            result = self.supabase.table("integration_credentials")\
                .update(update_data)\
                .eq("id", credential_id)\
                .execute()
            
            if result.data:
                self.logger.info(f"Successfully migrated credential {credential_id} (workspace: {workspace_id})")
                return True
            else:
                self.logger.error(f"Database update failed for credential {credential_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to migrate credential {credential_id}: {str(e)}")
            self.stats['errors'].append(f"Credential {credential_id}: {str(e)}")
            return False
    
    async def run_migration(self, force: bool = False) -> Dict[str, Any]:
        """
        Run the complete migration process.
        
        Args:
            force: Force migration even if some credentials appear encrypted
            
        Returns:
            Migration statistics and results
        """
        self.logger.info("Starting Jira API token encryption migration")
        self.logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        
        try:
            # Get all credentials
            credentials = await self.get_all_credentials()
            
            if not credentials:
                self.logger.info("No credentials found to migrate")
                return self.stats
            
            # Process in batches
            for i in range(0, len(credentials), self.batch_size):
                batch = credentials[i:i + self.batch_size]
                self.logger.info(f"Processing batch {(i // self.batch_size) + 1} ({len(batch)} credentials)")
                
                for credential in batch:
                    # Analyze credential
                    analysis = self.analyze_credential(credential)
                    
                    if analysis.get('is_encrypted'):
                        self.stats['already_encrypted'] += 1
                        if not force:
                            continue
                    
                    if analysis.get('error'):
                        self.stats['failed'] += 1
                        continue
                    
                    # Migrate credential
                    if await self.migrate_credential(credential, analysis):
                        self.stats['migrated'] += 1
                    else:
                        self.stats['failed'] += 1
                
                # Brief pause between batches
                if i + self.batch_size < len(credentials):
                    await asyncio.sleep(0.1)
            
            # Final statistics
            self.logger.info("Migration completed!")
            self.logger.info(f"Total credentials found: {self.stats['total_found']}")
            self.logger.info(f"Already encrypted: {self.stats['already_encrypted']}")
            self.logger.info(f"Successfully migrated: {self.stats['migrated']}")
            self.logger.info(f"Failed migrations: {self.stats['failed']}")
            
            if self.stats['errors']:
                self.logger.warning(f"Errors encountered: {len(self.stats['errors'])}")
                for error in self.stats['errors']:
                    self.logger.warning(f"  - {error}")
            
            return self.stats
            
        except Exception as e:
            self.logger.error(f"Migration failed: {str(e)}")
            raise
    
    async def validate_migration(self) -> Dict[str, Any]:
        """
        Validate that the migration was successful by checking all credentials.
        
        Returns:
            Validation results
        """
        self.logger.info("Validating migration results...")
        
        validation_stats = {
            'total_checked': 0,
            'properly_encrypted': 0,
            'validation_failed': 0,
            'errors': []
        }
        
        try:
            credentials = await self.get_all_credentials()
            validation_stats['total_checked'] = len(credentials)
            
            for credential in credentials:
                credential_id = credential.get('id', 'unknown')
                encrypted_token = credential.get('jira_api_token_encrypted', '')
                
                try:
                    # Check if it's properly encrypted
                    if self.encryption_service.is_encrypted(encrypted_token):
                        # Try to decrypt to verify it works
                        decrypted = self.encryption_service.decrypt(encrypted_token)
                        if decrypted:
                            validation_stats['properly_encrypted'] += 1
                        else:
                            validation_stats['validation_failed'] += 1
                            validation_stats['errors'].append(f"Credential {credential_id}: Decryption returned empty")
                    else:
                        validation_stats['validation_failed'] += 1
                        validation_stats['errors'].append(f"Credential {credential_id}: Not properly encrypted")
                
                except Exception as e:
                    validation_stats['validation_failed'] += 1
                    validation_stats['errors'].append(f"Credential {credential_id}: {str(e)}")
            
            self.logger.info("Validation completed!")
            self.logger.info(f"Total checked: {validation_stats['total_checked']}")
            self.logger.info(f"Properly encrypted: {validation_stats['properly_encrypted']}")
            self.logger.info(f"Validation failed: {validation_stats['validation_failed']}")
            
            return validation_stats
            
        except Exception as e:
            self.logger.error(f"Validation failed: {str(e)}")
            raise


def create_supabase_client() -> Client:
    """Create and return a Supabase client."""
    try:
        supabase_url = settings.SUPABASE_URL
        supabase_key = settings.SUPABASE_ANON_KEY
        
        if hasattr(supabase_url, 'get_secret_value'):
            supabase_url = supabase_url.get_secret_value()
        if hasattr(supabase_key, 'get_secret_value'):
            supabase_key = supabase_key.get_secret_value()
        
        return create_client(supabase_url, supabase_key)
    except Exception as e:
        print(f"Failed to create Supabase client: {e}")
        sys.exit(1)


async def main():
    """Main entry point for the migration script."""
    parser = argparse.ArgumentParser(description='Migrate Jira API tokens to encryption')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without applying them')
    parser.add_argument('--batch-size', type=int, default=10, help='Number of credentials to process at once')
    parser.add_argument('--force', action='store_true', help='Force migration even if credentials appear encrypted')
    parser.add_argument('--validate', action='store_true', help='Validate migration results')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Create Supabase client
    supabase_client = create_supabase_client()
    
    # Create migration script
    migration = CredentialMigrationScript(
        supabase_client=supabase_client,
        dry_run=args.dry_run,
        batch_size=args.batch_size
    )
    
    try:
        if args.validate:
            # Run validation only
            validation_results = await migration.validate_migration()
            if validation_results['validation_failed'] == 0:
                print("✅ All credentials are properly encrypted!")
                sys.exit(0)
            else:
                print(f"❌ {validation_results['validation_failed']} credentials failed validation")
                sys.exit(1)
        else:
            # Run migration
            migration_results = await migration.run_migration(force=args.force)
            
            if migration_results['failed'] == 0:
                print(f"✅ Migration completed successfully! {migration_results['migrated']} credentials migrated.")
                
                # Auto-validate if not dry run
                if not args.dry_run:
                    print("\n🔍 Running validation...")
                    validation_results = await migration.validate_migration()
                    if validation_results['validation_failed'] == 0:
                        print("✅ Validation passed - all credentials properly encrypted!")
                    else:
                        print(f"⚠️  Validation found issues with {validation_results['validation_failed']} credentials")
                
                sys.exit(0)
            else:
                print(f"⚠️  Migration completed with {migration_results['failed']} failures")
                sys.exit(1)
    
    except KeyboardInterrupt:
        print("\n🛑 Migration interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
