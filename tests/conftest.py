"""Global pytest fixtures and sys.path configuration for RACoN tests."""
import sys
from pathlib import Path

# Add project root directory to sys.path so 'import racon' resolves to the core racon package
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
