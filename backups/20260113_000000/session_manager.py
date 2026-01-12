# backend/reconciliation/session_manager.py
import os
import json
import pickle
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SessionManager:
    """Manages user sessions, data persistence, and file operations."""
    
    def __init__(self, base_data_dir: str = "data"):
        self.base_data_dir = Path(base_data_dir)
        self.base_data_dir.mkdir(exist_ok=True)
    
    def get_user_dir(self, username: str) -> Path:
        """Get or create user directory."""
        user_dir = self.base_data_dir / username
        user_dir.mkdir(exist_ok=True)
        return user_dir
    
    def create_session(self, username: str) -> str:
        """
        Create a new session folder with timestamp.
        Returns session_id (timestamp string).
        """
        user_dir = self.get_user_dir(username)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = user_dir / session_id
        
        # Create session structure
        (session_dir / "input").mkdir(parents=True, exist_ok=True)
        (session_dir / "output" / "results").mkdir(parents=True, exist_ok=True)
        
        return session_id
    
    def get_session_dir(self, username: str, session_id: str) -> Path:
        """Get session directory path."""
        return self.get_user_dir(username) / session_id
    
    def save_input_data(self, username: str, session_id: str, accounts: List[Dict], uploaded_files_data: Dict):
        """
        Save input data (accounts and uploaded files) to session input folder.
        
        Args:
            username: User's username
            session_id: Session identifier
            accounts: List of account dictionaries
            uploaded_files_data: Dictionary mapping file names to their content
        """
        session_dir = self.get_session_dir(username, session_id)
        input_dir = session_dir / "input"
        
        # Save accounts metadata
        accounts_meta = []
        for acc in accounts:
            accounts_meta.append({
                "bank_name": acc["bank_name"],
                "account_number": acc["account_number"],
                "files": [f.name for f in acc["files"]]
            })
        
        with open(input_dir / "accounts.json", "w") as f:
            json.dump(accounts_meta, f, indent=2)
        
        # Save uploaded CSV files
        files_dir = input_dir / "files"
        files_dir.mkdir(exist_ok=True)
        
        for filename, content in uploaded_files_data.items():
            with open(files_dir / filename, "wb") as f:
                f.write(content)
    
    def save_output_data(self, username: str, session_id: str, 
                         df_results: pd.DataFrame, 
                         pending_changes: Dict,
                         updated_pages: set,
                         page_number: int):
        """
        Save output data (results, pending changes, page state) to session output folder.
        
        Args:
            username: User's username
            session_id: Session identifier
            df_results: Results DataFrame
            pending_changes: Dictionary of pending GST category changes
            updated_pages: Set of updated page numbers
            page_number: Current page number
        """
        session_dir = self.get_session_dir(username, session_id)
        output_dir = session_dir / "output" / "results"
        
        # Save results DataFrame
        df_results.to_pickle(output_dir / "results.pkl")
        
        # Save session state
        session_state = {
            "pending_changes": {str(k): v for k, v in pending_changes.items()},
            "updated_pages": list(updated_pages),
            "page_number": page_number,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(output_dir / "session_state.json", "w") as f:
            json.dump(session_state, f, indent=2)
    
    def load_session_data(self, username: str, session_id: str) -> Optional[Dict]:
        """
        Load complete session data (input + output).
        
        Returns:
            Dictionary containing all session data or None if session doesn't exist
        """
        session_dir = self.get_session_dir(username, session_id)
        
        if not session_dir.exists():
            return None
        
        data = {
            "session_id": session_id,
            "accounts": None,
            "files_data": {},
            "results": None,
            "pending_changes": {},
            "updated_pages": set(),
            "page_number": 1
        }
        
        # Load accounts
        accounts_file = session_dir / "input" / "accounts.json"
        if accounts_file.exists():
            with open(accounts_file, "r") as f:
                data["accounts"] = json.load(f)
        
        # Load uploaded files
        files_dir = session_dir / "input" / "files"
        if files_dir.exists():
            for file_path in files_dir.glob("*"):
                with open(file_path, "rb") as f:
                    data["files_data"][file_path.name] = f.read()
        
        # Load results
        results_file = session_dir / "output" / "results" / "results.pkl"
        if results_file.exists():
            data["results"] = pd.read_pickle(results_file)
        
        # Load session state
        state_file = session_dir / "output" / "results" / "session_state.json"
        if state_file.exists():
            with open(state_file, "r") as f:
                state = json.load(f)
                # Convert string keys back to integers for pending_changes
                data["pending_changes"] = {int(k): v for k, v in state.get("pending_changes", {}).items()}
                data["updated_pages"] = set(state.get("updated_pages", []))
                data["page_number"] = state.get("page_number", 1)
        
        return data
    
    def get_all_sessions(self, username: str) -> List[Dict]:
        """
        Get list of all sessions for a user with metadata.
        
        Returns:
            List of dictionaries with session info, sorted by date (newest first)
        """
        user_dir = self.get_user_dir(username)
        sessions = []
        
        for session_dir in user_dir.iterdir():
            if session_dir.is_dir():
                try:
                    # Parse session_id (timestamp)
                    session_id = session_dir.name
                    dt = datetime.strptime(session_id, "%Y%m%d_%H%M%S")
                    
                    # Check if results exist
                    has_results = (session_dir / "output" / "results" / "results.pkl").exists()
                    
                    # Get last updated time
                    state_file = session_dir / "output" / "results" / "session_state.json"
                    last_updated = None
                    if state_file.exists():
                        with open(state_file, "r") as f:
                            state = json.load(f)
                            last_updated = state.get("last_updated")
                    
                    sessions.append({
                        "session_id": session_id,
                        "datetime": dt,
                        "display_name": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "has_results": has_results,
                        "last_updated": last_updated
                    })
                except Exception as e:
                    # Skip invalid session folders
                    continue
        
        # Sort by datetime, newest first
        sessions.sort(key=lambda x: x["datetime"], reverse=True)
        return sessions
    
    def get_latest_session(self, username: str) -> Optional[str]:
        """
        Get the latest session ID for a user.
        
        Returns:
            Session ID (timestamp string) or None if no sessions exist
        """
        sessions = self.get_all_sessions(username)
        return sessions[0]["session_id"] if sessions else None
    
    def delete_session(self, username: str, session_id: str) -> bool:
        """
        Delete a specific session.
        
        Returns:
            True if deleted successfully, False otherwise
        """
        import shutil
        session_dir = self.get_session_dir(username, session_id)
        
        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
                return True
            except Exception as e:
                print(f"Error deleting session: {e}")
                return False
        return False
    
    def save_pending_changes_only(self, username: str, session_id: str, 
                                   pending_changes: Dict, updated_pages: set, 
                                   page_number: int):
        """
        Quick save of just pending changes without full DataFrame save.
        Used for frequent updates during navigation.
        """
        session_dir = self.get_session_dir(username, session_id)
        output_dir = session_dir / "output" / "results"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        session_state = {
            "pending_changes": {str(k): v for k, v in pending_changes.items()},
            "updated_pages": list(updated_pages),
            "page_number": page_number,
            "last_updated": datetime.now().isoformat()
        }
        
        with open(output_dir / "session_state.json", "w") as f:
            json.dump(session_state, f, indent=2)


# Global instance
session_manager = SessionManager()