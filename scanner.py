"""
Ядро парсера: ходит в Telegram-ботов криптообменников через Telethon,
проходит сценарий покупки BTC (ввод суммы + адреса), собирает текст
интерфейса и определяет, есть ли ручной способ оплаты (СБП/карта + PDF-чек).

Поддерживает прокси (HTTP/SOCKS5) — берётся из .env переменной TG_PROXY.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from telethon import TelegramClient

try:
    import socks
except ImportError:
    socks = None

# ---------------------------------------------------------------------------
# Конфиг
# ---------------------------------------------------------------------------
API_ID = 0          # заполняется из .env
API_HASH = ""       # заполняется из .env
SESSION_DIR = Path(__file__).resolve().parent.parent / "sessions"
SESSION_DIR.mkdir(exist_ok=True)

BTC_ADDR = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"  # тестовый адрес

# Ключевые слова, указывающие на РУЧНУЮ оплату с чеком
MANUAL_SIGNALS = [
    "ручн", "вручн", "manual", "реквизит", "по реквизитам", "перевод на карту",
    "на карту", "сбер", "сбербанк", "тинькофф", "т-банк", "альфа", "сбп",
    "система быстрых", "по номеру телефона", "чек", "скриншот", "квитанц",
    "подтверждени", "приложите", "прикрепите", "pdf", "файл", "оператор",
    "менеджер", "в ручную", "ручная обработка", "ручное подтверждение",
    "ожидает подтверждения", "перевод на счёт", "перевод по сбп",
    "реквизиты для перевода", "платёж", "платеж", "оплатить переводом",
]

# Сигналы, которые прямо говорят о прикреплении чека/скрина
PDF_SIGNALS = [
    "pdf", "чек", "скриншот", "скрин", "квитанц", "приложите", "прикрепите",
    "прикрепить", "загрузите", "отправьте файл", "подтверждающий", "подтверждение оплаты",
    "фото чека", "фотографию чека", "документ", "файл",
]

# Ключевые слова, указывающие на АВТОМАТИЧЕСКИЙ способ (не наш случай)
AUTO_SIGNALS = [
    "автоматическ", "auto", "мгновен", "instant", "криптобот", "cryptobot",
    "wallet", "кошелёк telegram", "tonkeeper", "по балансу", "внутренний баланс",
    "оплата из баланса", "списание с баланса",
]

# Триггер-фразы, после которых бот обычно показывает способы оплаты
PAYMENT_TRIGGERS = [
    "способы оплаты", "способ оплаты", "методы оплаты", "метод оплаты",
    "выберите способ", "выберите метод", "оплатите", "оплата", "перевод",
    "реквизиты", "выберите вариант", "варианты оплаты", "доступные способы",
]

# Кнопки, по которым стоит кликать в сценарии покупки
CLICKABLE = [
    "купить", "обменять", "покупка", "buy", "exchange", "далее", "продолжить",
    "оплатить", "подтвердить", "выбрать", "ручной", "ручн", "вручную",
    "по реквизитам", "на карту", "сбп", "перевод", "оплата", "пополнить",
    "пополнение", "btc", "bitcoin", "биткоин", "криптовалют", "начать",
    "start", "меню", "главное меню", "карта", "реквизиты", "оператор",
    "связаться", "поддержка", "оформить", "создать заявку", "заявка",
]


def parse_proxy(proxy_str: str):
    """Парсит прокси из строки .env в формат Telethon.
    Поддерживает: http://user:pass@host:port, socks5://host:port, host:port"""
    if not proxy_str or socks is None:
        return None
    proxy_str = proxy_str.strip()
    if not proxy_str:
        return None

    # socks5://user:pass@host:port
    m = re.match(r"^(?:socks5|socks4|http|https)://(?:([^:@/]+):([^@/]+)@)?([^:/]+):(\d+)$", proxy_str)
    if m:
        user, pwd, host, port = m.groups()
        proto = proxy_str.split("://")[0].lower()
        if proto == "socks5":
            proxy_type = socks.SOCKS5
        elif proto == "socks4":
            proxy_type = socks.SOCKS4
        else:  # http/https через socks-совместимый режим
            proxy_type = socks.HTTP
        if user:
            return (proxy_type, host, int(port), True, user, pwd)
        return (proxy_type, host, int(port), True)
    # host:port
    m = re.match(r"^([^:/]+):(\d+)$", proxy_str)
    if m:
        host, port = m.groups()
        return (socks.SOCKS5, host, int(port), True)
    return None


@dataclass
class BotResult:
    username: str
    status: str = "pending"          # pending|ok|error|no_buy_flow|blocked
    title: str = ""
    has_manual_payment: bool = False
    has_pdf_check: bool = False
    payment_methods: list = field(default_factory=list)
    flow_log: list = field(default_factory=list)   # (шаг, текст) что бот отвечал
    error: str = ""
    scanned_at: float = field(default_factory=time.time)

    def to_dict(self):
        return asdict(self)


class TelegramBotScanner:
    """Обходит список ботов, гоняет сценарий покупки, собирает ответы."""

    def __init__(self, api_id: int, api_hash: str, session_name: str = "scanner",
                 proxy: str = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = parse_proxy(proxy) if proxy else None
        self.client = TelegramClient(
            str(SESSION_DIR / session_name), api_id, api_hash,
            proxy=self.proxy,
            connection_retries=2,
            timeout=20,
        )

    async def start(self):
        await self.client.start()

    async def stop(self):
        try:
            await self.client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Сценарий: /start -> /buy -> сумма -> адрес -> собрать способы оплаты
    # ------------------------------------------------------------------
    async def scan_bot(self, username: str, amount: str = "5000", timeout: int = 90) -> BotResult:
        res = BotResult(username=username)
        try:
            entity = await asyncio.wait_for(self.client.get_entity(username), timeout=30)
            res.title = getattr(entity, "title", "") or username
        except Exception as e:
            res.status = "error"
            res.error = f"get_entity: {e}"
            return res

        try:
            async with asyncio.timeout(timeout):
                # 1) /start
                await self._send_and_log(res, "/start", wait=1.5)
                # 2) пробуем /buy или кнопку купить
                await self._send_and_log(res, "/buy", wait=1.5)
                # 3) кликаем по кнопкам, если видим подходящие
                await self._click_buttons(res, max_clicks=4)
                # 4) если бот не понял /buy, попробуем "Купить"
                if not self._looks_like_flow(res):
                    await self._send_and_log(res, "Купить", wait=1.5)
                    await self._click_buttons(res, max_clicks=3)
                # 5) сумма
                await self._send_and_log(res, amount, wait=1.5)
                # 6) адрес (если спросит)
                await self._send_and_log(res, BTC_ADDR, wait=2.0)
                # 7) пробуем нажать кнопки "Далее/Продолжить/Оплатить"
                await self._click_buttons(res, max_clicks=4)
                # 8) если дошли до способов оплаты - кликаем по ручным
                await self._click_manual_buttons(res, max_clicks=3)
                # 9) финальный сбор
                await self._collect_payment_methods(res)
        except asyncio.TimeoutError:
            res.status = "error"
            res.error = "timeout"
        except Exception as e:
            res.status = "error"
            res.error = str(e)

        if res.status != "error":
            res.status = "ok" if res.has_manual_payment else "no_buy_flow"
        return res

    async def _send_and_log(self, res: BotResult, text: str, wait: float = 1.0):
        try:
            await self.client.send_message(res.username, text)
            await asyncio.sleep(wait)
            msgs = await self._get_recent_messages(res.username)
            for m in msgs:
                res.flow_log.append({"step": text, "text": m[:500]})
        except Exception as e:
            res.flow_log.append({"step": text, "error": str(e)})

    async def _get_recent_messages(self, username: str, limit: int = 8):
        out = []
        try:
            async for msg in self.client.iter_messages(username, limit=limit):
                if msg.out:
                    continue
                txt = msg.text or ""
                if txt:
                    out.append(txt)
                if msg.buttons:
                    for row in msg.buttons:
                        for btn in row:
                            out.append(f"[BUTTON] {btn.text}")
        except Exception:
            pass
        return out

    async def _click_buttons(self, res: BotResult, max_clicks: int = 3):
        """Кликает по inline-кнопкам, подходящим под сценарий покупки."""
        for _ in range(max_clicks):
            try:
                msgs = await self._get_recent_messages(res.username, limit=3)
                clicked = False
                for m in msgs:
                    if m.startswith("[BUTTON]"):
                        btn_text = m.replace("[BUTTON] ", "").strip()
                        if self._is_clickable(btn_text):
                            try:
                                # ищем сообщение с кнопкой и кликаем по ней
                                async for msg in self.client.iter_messages(res.username, limit=5):
                                    if msg.buttons:
                                        for row in msg.buttons:
                                            for btn in row:
                                                if btn.text.strip().lower() == btn_text.lower():
                                                    await msg.click(text=btn.text)
                                                    await asyncio.sleep(1.5)
                                                    res.flow_log.append(
                                                        {"step": f"click:{btn_text}", "text": "pressed"}
                                                    )
                                                    clicked = True
                                                    break
                                            if clicked:
                                                break
                                    if clicked:
                                        break
                            except Exception as e:
                                res.flow_log.append({"step": f"click:{btn_text}", "error": str(e)})
                if not clicked:
                    break
            except Exception:
                break

    async def _click_manual_buttons(self, res: BotResult, max_clicks: int = 3):
        """Кликает по кнопкам ручной оплаты (СБП/карта/реквизиты/чек)."""
        manual_keywords = ["ручн", "реквизит", "на карту", "сбп", "перевод", "карта",
                           "оператор", "менеджер", "чек", "вручную", "в ручную"]
        for _ in range(max_clicks):
            try:
                msgs = await self._get_recent_messages(res.username, limit=3)
                clicked = False
                for m in msgs:
                    if m.startswith("[BUTTON]"):
                        btn_text = m.replace("[BUTTON] ", "").strip().lower()
                        if any(k in btn_text for k in manual_keywords):
                            async for msg in self.client.iter_messages(res.username, limit=5):
                                if msg.buttons:
                                    for row in msg.buttons:
                                        for btn in row:
                                            if btn.text.strip().lower() == btn_text:
                                                await msg.click(text=btn.text)
                                                await asyncio.sleep(1.5)
                                                res.flow_log.append(
                                                    {"step": f"click_manual:{btn.text}", "text": "pressed"}
                                                )
                                                clicked = True
                                                break
                                        if clicked:
                                            break
                                if clicked:
                                    break
                if not clicked:
                    break
            except Exception:
                break

    def _is_clickable(self, btn_text: str) -> bool:
        t = btn_text.lower()
        # короткие кнопки-эмодзи или навигация
        if len(t) < 2:
            return False
        return any(k in t for k in CLICKABLE)

    def _looks_like_flow(self, res: BotResult) -> bool:
        joined = " ".join(x.get("text", "") for x in res.flow_log).lower()
        return any(k in joined for k in ["сумм", "адрес", "btc", "биткоин", "купить", "обмен"])

    # ------------------------------------------------------------------
    # Анализ собранного текста
    # ------------------------------------------------------------------
    async def _collect_payment_methods(self, res: BotResult):
        joined = " ".join(x.get("text", "") for x in res.flow_log).lower()
        # ищем способы оплаты
        methods = set()
        for kw in ["сбп", "карта", "сбер", "тинькофф", "альфа", "реквизит",
                   "баланс", "криптобот", "ton", "usdt", "автоматическ", "ручн"]:
            if kw in joined:
                methods.add(kw)
        res.payment_methods = sorted(methods)

        # ручная оплата?
        manual_hits = [s for s in MANUAL_SIGNALS if s in joined]
        strong_manual = any(s in joined for s in [
            "ручн", "реквизит", "на карту", "сбп", "приложите", "чек", "вручную"
        ])
        res.has_manual_payment = len(manual_hits) >= 2 or strong_manual

        # PDF-чек?
        pdf_hits = [s for s in PDF_SIGNALS if s in joined]
        res.has_pdf_check = len(pdf_hits) >= 2 or any(s in joined for s in [
            "pdf", "чек", "скриншот", "квитанц", "приложите", "прикрепите"
        ])

        # лог для отладки
        res.flow_log.append({
            "step": "ANALYSIS",
            "text": f"manual={res.has_manual_payment} pdf={res.has_pdf_check} "
                    f"methods={res.payment_methods} manual_hits={manual_hits}"
        })