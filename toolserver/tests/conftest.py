import sys
from pathlib import Path

# Import from src/ directly. The editable-install .pth file is skipped when macOS marks
# it hidden (as it does in some synced folders), so don't rely on it.
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
