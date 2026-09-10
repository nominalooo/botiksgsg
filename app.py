"""
Web UI парсера криптообменников Telegram.
FastAPI + статический фронт. Запуск: uvicorn main:app --port 8000
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import discovery, scanner

logger = logging.getLogger("app")

BASE_DIR = Path(__file__).resolve().parent

try:
    load_dotenv(BASE_DIR / ".env")
except Exception as e:
    logger.warning("Failed to load .env file: %s", e)

app = FastAPI(title="Crypto TG Scanner", version="2.0")

# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------
SCAN_STATE = {
    "running": False,
    "infinite": False,          # бесконечный режим discover->scan->discover...
    "progress": 0,
    "total": 0,
    "current": "",
    "started_at": None,
    "log": [],
    "results": [],
    "cycles": 0,
}


def _log(msg: str):
    SCAN_STATE["log"].append({"ts": time.time(), "msg": msg})
    SCAN_STATE["log"] = SCAN_STATE["log"][-300:]


def _tg_creds():
    api_id = int(os.getenv("TG_API_ID", "0") or "0")
    api_hash = os.getenv("TG_API_HASH", "") or ""
    return api_id, api_hash


def _proxy():
    return os.getenv("TG_PROXY", "") or None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = BASE_DIR / "static" / "index.html"
    if not index_path.exists():
        return HTMLResponse(
            content="<h1>Crypto TG Scanner</h1><p>Static UI not found.</p>",
            status_code=200,
        )
    return index_path.read_text(encoding="utf-8")


@app.get("/api/status")
async def status():
    api_id, api_hash = _tg_creds()
    return {
        "state": SCAN_STATE,
        "api_configured": bool(api_id and api_hash),
        "proxy": bool(_proxy()),
        "bots": discovery.load_bots(),
        "results": discovery.load_results(),
    }


@app.post("/api/discover")
async def api_discover():
    """Запускает поиск новых ботов в вебе (включая Exa)."""
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, discovery.run_discovery_exa)
    return res


@app.post("/api/scan")
async def api_scan(body: dict):
    """Запускает скан ботов. body: {amount: "5000", usernames: [...] | null}"""
    if SCAN_STATE["running"]:
        raise HTTPException(400, "Scan already running")

    api_id, api_hash = _tg_creds()
    if not api_id or not api_hash:
        raise HTTPException(400, "TG_API_ID/TG_API_HASH не настроены в .env")

    usernames = body.get("usernames") or [b["username"] for b in discovery.load_bots()]
    amount = str(body.get("amount", "5000"))

    if not usernames:
        raise HTTPException(400, "Нет ботов для скана")

    SCAN_STATE.update({
        "running": True,
        "infinite": False,
        "progress": 0,
        "total": len(usernames),
        "current": "",
        "started_at": time.time(),
        "log": [],
        "results": [],
        "cycles": 0,
    })
    _log(f"Старт скана: {len(usernames)} ботов, сумма {amount}")

    async def worker():
        try:
            scanner_obj = scanner.TelegramBotScanner(api_id, api_hash, proxy=_proxy())
            await scanner_obj.start()
            for i, u in enumerate(usernames):
                if not SCAN_STATE["running"]:
                    break
                SCAN_STATE["current"] = u
                SCAN_STATE["progress"] = i
                _log(f"[{i+1}/{len(usernames)}] {u}")
                try:
                    r = await scanner_obj.scan_bot(u, amount=amount)
                except Exception as e:
                    r = scanner.BotResult(username=u, status="error", error=str(e))
                SCAN_STATE["results"].append(r.to_dict())
                _log(f"  -> {r.status} | manual={r.has_manual_payment} | pdf={r.has_pdf_check}")
                # сохраняем результаты инкрементально
                results = discovery.load_results()
                results[u] = r.to_dict()
                discovery.save_results(results)
            await scanner_obj.stop()
        except Exception as e:
            _log(f"FATAL: {e}")
        finally:
            SCAN_STATE["running"] = False
            SCAN_STATE["progress"] = SCAN_STATE["total"]
            _log("Скан завершён")

    asyncio.create_task(worker())
    return {"started": True, "total": len(usernames)}


@app.post("/api/scan_infinite")
async def api_scan_infinite(body: dict):
    """Бесконечный режим: discover → scan новых → discover → scan..."""
    if SCAN_STATE["running"]:
        raise HTTPException(400, "Scan already running")

    api_id, api_hash = _tg_creds()
    if not api_id or not api_hash:
        raise HTTPException(400, "TG_API_ID/TG_API_HASH не настроены в .env")

    amount = str(body.get("amount", "5000"))
    delay = int(body.get("delay", 120))          # пауза между циклами discover
    max_cycles = int(body.get("max_cycles", 0))  # 0 = бесконечно

    SCAN_STATE.update({
        "running": True,
        "infinite": True,
        "progress": 0,
        "total": 0,
        "current": "",
        "started_at": time.time(),
        "log": [],
        "results": [],
        "cycles": 0,
    })
    _log(f"Бесконечный режим: сумма {amount}, пауза {delay}с, циклы {max_cycles or '∞'}")

    async def worker():
        cycle = 0
        try:
            scanner_obj = scanner.TelegramBotScanner(api_id, api_hash, proxy=_proxy())
            await scanner_obj.start()
            while SCAN_STATE["running"]:
                cycle += 1
                SCAN_STATE["cycles"] = cycle
                _log(f"=== Цикл {cycle}: поиск новых ботов ===")
                # 1) discover
                try:
                    d = await asyncio.get_running_loop().run_in_executor(
                        None, discovery.run_discovery_exa
                    )
                    _log(f"  найдено {len(d['found'])}, добавлено {d['added']}, "
                         f"в базе {d['total_in_db']}")
                except Exception as e:
                    _log(f"  discover error: {e}")
                    d = {"added": 0, "total_in_db": len(discovery.load_bots())}

                # 2) скан всех ботов (включая только что добавленных)
                bots = discovery.load_bots()
                usernames = [b["username"] for b in bots]
                if not usernames:
                    _log("  база пуста, ждём...")
                    await asyncio.sleep(delay)
                    continue

                SCAN_STATE["total"] = len(usernames)
                for i, u in enumerate(usernames):
                    if not SCAN_STATE["running"]:
                        break
                    SCAN_STATE["current"] = u
                    SCAN_STATE["progress"] = i
                    _log(f"  [{i+1}/{len(usernames)}] {u}")
                    try:
                        r = await scanner_obj.scan_bot(u, amount=amount)
                    except Exception as e:
                        r = scanner.BotResult(username=u, status="error", error=str(e))
                    SCAN_STATE["results"].append(r.to_dict())
                    # обновляем статус бота в базе
                    for b in bots:
                        if b["username"] == u:
                            b["status"] = r.status
                            b["scans"] = b.get("scans", 0) + 1
                            b["last_scan"] = time.time()
                    discovery.save_bots(bots)
                    # сохраняем результаты
                    results = discovery.load_results()
                    results[u] = r.to_dict()
                    discovery.save_results(results)
                    if r.has_manual_payment:
                        _log(f"    ★ MANUAL: @{u} (pdf={r.has_pdf_check})")

                if not SCAN_STATE["running"]:
                    break
                if max_cycles and cycle >= max_cycles:
                    _log(f"Достигнут лимит циклов ({max_cycles})")
                    break
                _log(f"  пауза {delay}с до следующего цикла...")
                # ждём с возможностью остановки
                for _ in range(delay):
                    if not SCAN_STATE["running"]:
                        break
                    await asyncio.sleep(1)
            await scanner_obj.stop()
        except Exception as e:
            _log(f"FATAL: {e}")
            try:
                await scanner_obj.stop()
            except Exception:
                pass
        finally:
            SCAN_STATE["running"] = False
            SCAN_STATE["infinite"] = False
            _log("Бесконечный режим остановлен")

    asyncio.create_task(worker())
    return {"started": True, "mode": "infinite"}


@app.post("/api/stop")
async def api_stop():
    SCAN_STATE["running"] = False
    return {"stopped": True}


@app.get("/api/results")
async def api_results():
    return discovery.load_results()


@app.post("/api/bots/add")
async def api_add_bot(body: dict):
    username = str(body.get("username", "")).strip().lstrip("@").lower()
    if not username:
        raise HTTPException(400, "empty username")
    added = discovery.merge_bots([username])
    return {"added": added, "total": len(discovery.load_bots())}


@app.post("/api/bots/remove")
async def api_remove_bot(body: dict):
    username = str(body.get("username", "")).strip()
    bots = [b for b in discovery.load_bots() if b["username"] != username]
    discovery.save_bots(bots)
    return {"ok": True, "total": len(bots)}


@app.get("/api/bots")
async def api_bots():
    return discovery.load_bots()


@app.get("/api/export")
async def api_export():
    """Экспорт результатов в JSON (для скачивания)."""
    return JSONResponse(
        content={
            "exported_at": time.time(),
            "bots": discovery.load_bots(),
            "results": discovery.load_results(),
        },
        headers={"Content-Disposition": 'attachment; filename="scan_results.json"'},
    )


# статика
try:
    static_dir = BASE_DIR / "static"
    if not static_dir.exists():
        logger.warning("Static directory %s does not exist, creating it", static_dir)
        static_dir.mkdir(parents=True, exist_ok=True)

    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        logger.warning("Static path %s is not a directory, skipping mount", static_dir)
except Exception as e:
    logger.warning("Failed to mount static files: %s", e)