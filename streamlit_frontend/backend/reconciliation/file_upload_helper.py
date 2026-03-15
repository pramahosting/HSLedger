# backend/reconciliation/file_upload_helper.py
import io
from typing import List, Dict
import streamlit as st


class MockUploadedFile:
    """Mock UploadedFile object to recreate file uploads from saved data."""
    
    def __init__(self, name: str, content: bytes):
        self.name = name
        self.content = content
        self._position = 0
    
    def read(self, size=-1):
        """Read file content."""
        if size == -1:
            result = self.content[self._position:]
            self._position = len(self.content)
        else:
            result = self.content[self._position:self._position + size]
            self._position += size
        return result
    
    def seek(self, position, whence=0):
        """Seek to position in file."""
        if whence == 0:  # absolute
            self._position = position
        elif whence == 1:  # relative
            self._position += position
        elif whence == 2:  # from end
            self._position = len(self.content) + position
        return self._position
    
    def tell(self):
        """Get current position."""
        return self._position
    
    def getvalue(self):
        """Get full content."""
        return self.content


def create_mock_files_from_data(files_data: Dict[str, bytes]) -> List[MockUploadedFile]:
    """
    Create mock uploaded file objects from saved data.
    
    Args:
        files_data: Dictionary mapping filename to file content bytes
        
    Returns:
        List of MockUploadedFile objects
    """
    return [MockUploadedFile(name, content) for name, content in files_data.items()]


def reconstruct_accounts_with_files(accounts_metadata: List[Dict], 
                                    files_data: Dict[str, bytes]) -> List[Dict]:
    """
    Reconstruct account dictionaries with mock file objects.
    
    Args:
        accounts_metadata: List of account metadata dictionaries
        files_data: Dictionary of file contents
        
    Returns:
        List of account dictionaries with file objects
    """
    reconstructed_accounts = []
    
    for acc_meta in accounts_metadata:
        account = {
            "bank_name": acc_meta["bank_name"],
            "account_number": acc_meta["account_number"],
            "files": []
        }
        
        # Create mock files for this account
        for filename in acc_meta["files"]:
            if filename in files_data:
                mock_file = MockUploadedFile(filename, files_data[filename])
                account["files"].append(mock_file)
        
        reconstructed_accounts.append(account)
    
    return reconstructed_accounts


def save_uploaded_files_to_dict(accounts: List[Dict]) -> Dict[str, bytes]:
    """
    Extract file contents from uploaded files and store in dictionary.
    
    Args:
        accounts: List of account dictionaries with file objects
        
    Returns:
        Dictionary mapping filename to file content bytes
    """
    files_data = {}
    
    for acc in accounts:
        for file in acc.get("files", []):
            file.seek(0)
            content = file.read()
            files_data[file.name] = content
            file.seek(0)
    
    return files_data