"""conftest — 把 src/ 加进 sys.path，让 from controlplane / from l1_agent 能 import。"""

import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 测试默认走 mock MCP
os.environ.setdefault("MCP_TRANSPORT", "mock")