"""让协议层脱离 Home Assistant 导入的引导脚本（仅供测试使用）。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_PKG = ROOT / "custom_components" / "hdl_buspro"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hdl_buspro" not in sys.modules:
    pkg = types.ModuleType("hdl_buspro")
    pkg.__path__ = [str(SKILL_PKG)]  # type: ignore[attr-defined]
    sys.modules["hdl_buspro"] = pkg
