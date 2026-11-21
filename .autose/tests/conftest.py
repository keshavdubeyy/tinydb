import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTOSE_OUT"] = tempfile.mkdtemp(prefix="autose_out_")
for k in ("GH_REPO", "GITHUB_REPOSITORY", "GH_TOKEN", "GITHUB_TOKEN", "AUTOSE_SKIP_CI"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
