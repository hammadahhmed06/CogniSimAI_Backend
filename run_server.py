# run_server.py
# Script to run the CogniSim AI backend server with proper environment setup

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    
    # Run the server
    uvicorn.run(
        "app.main:app",  # Use import string for reload
        host="localhost",  # localhost for easier access
        port=8000,
        reload=True,  # Enable reload for development
        log_level="info"
    )
