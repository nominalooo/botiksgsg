"""
Точка входа Web UI. Запуск:  uvicorn main:app --host 127.0.0.1 --port 8000
Или:                            python main.py
"""
import os
import sys
from pathlib import Path

# чтобы `from core import ...` работал при запуске из любой директории
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Crypto TG Scanner → http://{host}:{port}\n")
    uvicorn.run("main:app", host=host, port=port, reload=False)