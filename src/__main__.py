"""
让 `python src/` 与 `python -m src` 都能启动应用。

调用 `src/main.py` 的 main()。
"""
import sys
from .main import main

if __name__ == "__main__":
    sys.exit(main())
