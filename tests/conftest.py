import sys
from pathlib import Path

# Ensure repository root is on sys.path so top-level 'scripts' package is importable
# alongside the src/ layout. This mirrors how users run scripts directly.
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
