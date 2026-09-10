"""
Модуль обнаружения криптообменников в Telegram.
Ищет ботов через веб-источники (статьи, каталоги, мониторинги),
извлекает t.me-username, фильтрует мусор и пополняет базу.
"""
import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
BOTS_FILE = DATA_DIR / "bots.json"
RESULTS_FILE = DATA_DIR / "results.json"

# Источники: страницы-каталоги и статьи про обменники в Telegram
SOURCES = [
    # каталоги обменников
    "https://coindaily.ru/obmenniki/sbp-btc/",
    "https://vc.ru/top-raiting/3044670-obmen-kriptovalyuty-v-telegram",
    "https://top-ex.org/blog/obmenniki-kriptovalyuty-v-telegram-boty-kak-vybrat/",
    "https://wordcripta.ru/raznoe/luchshie-telegramm-boty-obmen-kriptovalyuty-2025",
    "https://dtf.ru/crypto-top/5034236-reyting-luchshikh-kriptovalyutnykh-botov-v-telegram",
    "https://altcoinlog.com/top-telegram-botov-dlya-pokupki-kriptovalyuty/",
    "https://mco-nn.ru/bitkoin-obmennik-v-tg-lychshie-boty-telegram-dlia-obmena-i-torgovli-kriptovalutoi-v-2026-gody/",
    "https://coinspot.io/cryptocurrencies/altcoins/bitkoin-obmennik-telegram-boty/",
    "https://vc.ru/crypto/1857277-luchshie-boty-v-telegram-dlya-obmena-i-torgovli-kriptovalyutoi-v-2026-godu",
    "https://crystal-trade.org/ru/telegram-obmen-kriptovalyut/",
]

# Поисковые запросы для расширения базы
SEARCH_QUERIES = [
    "telegram bot обменник биткоин купить ручной способ оплаты чек",
    "telegram bot обменник крипта СБП карта реквизиты pdf чек",
    "t.me бот купить btc за рубли по реквизитам",
    "лучшие telegram боты обменники криптовалют список",
    "обменник в телеграм бот bitcoin ручная оплата оператор",
]

STOP_WORDS = {
    "topics","context","graph","type","wallet","cryptobot","botfather","telegram",
    "tme","share","iv","joinchat","proxy","s","c","spambot","premium","stars",
    "boost","gift","invoice","payments","support","username","link","url","https",
    "http","com","ru","org","net","html","php","index","main","page","site","web",
    "app","bot","bots","channel","group","chat","user","users","message","messages",
    "photo","video","file","files","document","docs","audio","voice","sticker",
    "stickers","animation","contact","location","venue","poll","quiz","game","dice",
    "dart","emoji","gif","inline","callback","keyboard","button","buttons","menu",
    "start","help","settings","admin","owner","creator","member","members","join",
    "leave","kick","ban","unban","mute","unmute","pin","unpin","delete","edit",
    "send","receive","reply","forward","search","find","list","show","view","open",
    "close","create","make","new","add","remove","update","change","set","get",
    "check","verify","confirm","cancel","submit","back","next","prev","first",
    "last","top","best","good","bad","free","paid","buy","sell","trade","exchange",
    "swap","convert","transfer","withdraw","deposit","balance","address","amount",
    "price","rate","fee","commission","limit","min","max","total","sum","count",
    "number","code","key","token","api","auth","login","sign","register","account",
    "password","email","phone","sms","otp","2fa","kyc","aml","card","bank","sbp",
    "crypto","bitcoin","btc","usdt","usdc","eth","ton","trx","xrp","ltc","doge",
    "sol","bnb","matic","avax","dot","ada","link","uni","aave","cake","sushi",
    "pancake","uniswap","binance","bybit","okx","huobi","kraken","coinbase","gate",
    "mexc","bingx","bitget","bitpapa","prostoex","onelabel","exchanger","exchange",
    "monitor","bestchange","changelly","changenow","fixedfloat","simpleswap",
    "stealthex","letsexchange","exolix","godex","swipelux","trocador","orange",
    "p2p","otc","manual","auto","instant","fast","quick","secure","safe","trust",
    "ledger","trezor","metamask","trustwallet","electrum","exodus","atomic","guarda",
    "coinomi","jaxx","bread","brd","edge","zengo","unstoppable","phantom","solflare",
    "tokenpocket","imtoken","mathwallet","safepal","ellipal","coldcard","bitbox",
    "keepkey","cobo","dcent","secux","tangem","coolwallet","onekey","cypherock",
    "ngrave","keystone","passport","foundation","bitlox","opendime","wasabi",
    "samourai","cakewallet","bluewallet","greenwallet","specter","sparrow","mempool",
    "blockstream","blockchair","blockcypher","chain","network","node","miner",
    "mining","hash","block","transaction","tx","satoshi","sats","wei","gwei","gas",
    "nonce","signature","private","public","seed","phrase","mnemonic","recovery",
    "backup","restore","import","export","paper","hardware","software","hot","cold",
    "custodial","noncustodial","dex","cex","defi","nft","coin","altcoin",
    "stablecoin","fiat","rub","usd","eur","cny","inr","uah","kzt","gel","amd",
    "try","aed","sar","qar","kwd","bhd","omr","jod","ils","egp","ngn","kes","zar",
    "brl","mxn","ars","clp","cop","pen","uyu","ves","bob","pyg","pkr","bdt","lkr",
    "npr","mmk","khr","idr","myr","php","sgd","thb","vnd","twd","hkd","mop","krw",
    "jpy","aud","nzd","cad","chf","nok","sek","dkk","isk","pln","czk","huf","ron",
    "bgn","hrk","rsd","mkd","all","bam","mdl","azn","kgs","tjs","tmt","uzs","mnt",
    "byn","gbp","zar","try","ils","ngn","kes","ars","clp","cop","pen","uyu","ves",
    "bob","pyg","pkr","bdt","lkr","npr","mmk","khr","idr","myr","php","sgd","thb",
    "vnd","twd","hkd","mop","krw","jpy","aud","nzd","cad","chf","nok","sek","dkk",
    "isk","pln","czk","huf","ron","bgn","hrk","rsd","mkd","all","bam","mdl","azn",
    "kgs","tjs","tmt","uzs","mnt","byn",
}


def load_bots() -> list:
    if BOTS_FILE.exists():
        data = json.loads(BOTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    return []


def save_bots(bots: list):
    BOTS_FILE.write_text(json.dumps(bots, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results() -> dict:
    if RESULTS_FILE.exists():
        data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def save_results(results: dict):
    RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_username(u: str) -> bool:
    u = u.strip().lower()
    if len(u) < 4 or len(u) > 40:
        return False
    if not re.fullmatch(r"[a-z0-9_]+", u):
        return False
    if u in STOP_WORDS:
        return False
    if u.isdigit():
        return False
    # бот должен оканчиваться на bot ИЛИ быть похожим на обменник
    if u.endswith("bot"):
        return True
    if any(k in u for k in ["ex", "change", "obmen", "btc", "crypto", "coin", "pay", "wallet", "swap"]):
        return True
    return False


def extract_bots_from_html(html: str) -> set:
    found = set()
    for m in re.findall(r't\.me/([A-Za-z0-9_]+)', html):
        if _clean_username(m):
            found.add(m.lower())
    for m in re.findall(r'@([A-Za-z0-9_]{4,})', html):
        if _clean_username(m):
            found.add(m.lower())
    return found


def fetch(url: str, timeout: int = 25) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "ru,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.text
    except Exception:
        return ""


def discover_from_sources() -> list:
    """Собирает ботов из статей и каталогов."""
    found = set()
    for url in SOURCES:
        html = fetch(url)
        if not html:
            continue
        found |= extract_bots_from_html(html)
        time.sleep(0.4)
    return sorted(found)


def discover_from_search() -> list:
    """Ищет ботов через поисковики (HTML-версия)."""
    found = set()
    for q in SEARCH_QUERIES:
        for engine, url in [
            ("google", f"https://www.google.com/search?q={quote_plus(q)}&num=20"),
            ("bing", f"https://www.bing.com/search?q={quote_plus(q)}&count=20"),
            ("duckduckgo", f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"),
        ]:
            try:
                html = fetch(url)
                found |= extract_bots_from_html(html)
            except Exception:
                pass
            time.sleep(0.5)
    return sorted(found)


def merge_bots(new_bots: list) -> int:
    """Добавляет новых ботов в базу, возвращает сколько добавлено."""
    bots = load_bots()
    existing = {b["username"] for b in bots}
    added = 0
    for u in new_bots:
        if u not in existing:
            bots.append({
                "username": u,
                "source": "web",
                "added_at": time.time(),
                "status": "new",
                "scans": 0,
            })
            existing.add(u)
            added += 1
    save_bots(bots)
    return added


def run_discovery() -> dict:
    """Полный цикл обнаружения."""
    from_sources = discover_from_sources()
    from_search = discover_from_search()
    all_found = sorted(set(from_sources) | set(from_search))
    added = merge_bots(all_found)
    return {
        "found": all_found,
        "added": added,
        "total_in_db": len(load_bots()),
        "from_sources": from_sources,
        "from_search": from_search,
    }


if __name__ == "__main__":
    res = run_discovery()
    print(json.dumps(res, ensure_ascii=False, indent=2))

# ---------------------------------------------------------------------------
# Exa-поиск (нейросетевой) — находит ботов по смыслу, а не только по ключам
# ---------------------------------------------------------------------------
EXA_QUERIES = [
    "telegram bot обменник криптовалют купить btc за рубли ручной способ оплаты чек pdf",
    "телеграм бот обменник биткоин сбп карта реквизиты приложить чек",
    "best telegram crypto exchange bots manual payment receipt",
    "t.me bot buy bitcoin rub bank card receipt manual operator",
    "обменник валют телеграм бот ручная оплата оператор чек скриншот",
]


def discover_exa() -> list:
    """Ищет ботов через Exa (нейросетевой поиск по смыслу)."""
    try:
        from exa_py import Exa
    except ImportError:
        return []

    api_key = None
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("EXA_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not api_key:
        api_key = __import__("os").environ.get("EXA_API_KEY", "")

    if not api_key:
        return []

    found = set()
    try:
        exa = Exa(api_key)
        for q in EXA_QUERIES:
            try:
                res = exa.search(q, num_results=10, type="auto")
                for r in getattr(res, "results", []):
                    url = getattr(r, "url", "") or ""
                    title = getattr(r, "title", "") or ""
                    text = getattr(r, "text", "") or ""
                    blob = f"{url} {title} {text}"
                    found |= extract_bots_from_html(blob)
            except Exception:
                continue
            time.sleep(0.3)
    except Exception:
        pass
    return sorted(found)


def run_discovery_exa() -> dict:
    """Полный цикл обнаружения (включая Exa)."""
    from_sources = discover_from_sources()
    from_search = discover_from_search()
    from_exa = discover_exa()
    all_found = sorted(set(from_sources) | set(from_search) | set(from_exa))
    added = merge_bots(all_found)
    return {
        "found": all_found,
        "added": added,
        "total_in_db": len(load_bots()),
        "from_sources": from_sources,
        "from_search": from_search,
        "from_exa": from_exa,
    }


def run_infinite(cycles: int = 0, delay: int = 60, on_progress=None):
    """Бесконечный цикл: discover → (пауза) → discover...
    cycles=0 означает бесконечно. on_progress вызывается после каждого цикла."""
    total_added = 0
    cycle = 0
    while True:
        cycle += 1
        try:
            res = run_discovery_exa()
            total_added += res["added"]
            if on_progress:
                on_progress(cycle, res)
            print(f"[cycle {cycle}] found={len(res['found'])} added={res['added']} "
                  f"total={res['total_in_db']}")
        except Exception as e:
            if on_progress:
                on_progress(cycle, {"error": str(e)})
            print(f"[cycle {cycle}] ERROR: {e}")
        if cycles and cycle >= cycles:
            break
        time.sleep(delay)
    return {"cycles": cycle, "total_added": total_added}
