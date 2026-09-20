"""Naiz Studio 的命令列入口:給其他程式呼叫用,不會打開視窗。說明見 core/cli.py。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
