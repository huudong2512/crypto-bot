import os
import json
import time
import logging
import threading
from collections import deque
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import websocket
from dotenv import load_dotenv

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG & THÔNG SỐ CHIẾN LƯỢC
# ==========================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Danh sách coin theo dõi & mốc hỗ trợ đáy cứng ban đầu (có thể sửa trực tiếp qua Telegram)
SUPPORT_LEVELS = {
    "BTCUSDT": 62006.0,
    "ETHUSDT": 1765.0,
}

# Ngưỡng cảnh báo thanh lý Long trong 15 phút (USD)
LIQ_SPIKE_THRESHOLD_USD = {
    "BTCUSDT": 1_000_000.0,
    "ETHUSDT": 500_000.0,
}

# Tham số lọc tín hiệu Wyckoff 2.0
SCAN_INTERVAL_SEC = 60          # Quét dữ liệu mỗi 60 giây
ALERT_COOLDOWN_SEC = 1800       # Chống spam: mỗi loại tín hiệu cách nhau tối thiểu 30 phút
MIN_WICK_RATIO = 0.50           # Râu dưới tối thiểu 50% tổng chiều dài cây nến
VOL_SPIKE_MULTIPLIER = 1.6      # Volume nến rút chân >= 1.6 lần trung bình 20 nến
OI_DROP_PCT_THRESHOLD = -0.30   # OI giảm ít nhất 0.30% so với đỉnh 30 phút gần nhất
OI_SURGE_PCT_THRESHOLD = 0.30   # OI tăng ít nhất +0.30% trong 15 phút khi phá đáy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("WyckoffScanner")

state_lock = threading.Lock()
oi_history = {sym: deque(maxlen=60) for sym in SUPPORT_LEVELS}  # Lưu tối đa 60 phút OI
last_alert_time = {}


# ==========================================
# 2. MODULE GỬI & NHẬN LỆNH TELEGRAM
# ==========================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Lỗi gửi Telegram: {resp.text}")
    except Exception as e:
        logger.error(f"Exception gửi Telegram: {e}")


# ==========================================
# 3. WEBSOCKET THANH LÝ BINANCE (CHẠY NGẦM)
# ==========================================
class LiquidationTracker:
    def __init__(self, symbols):
        self.symbols = set(symbols)
        self.events = deque()  # (timestamp, symbol, liq_side, usd_val)
        self.lock = threading.Lock()
        self.ws_url = "wss://fstream.binance.com/ws/!forceOrder@arr"

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            order = data.get("o", {})
            sym = order.get("s")
            if sym not in self.symbols:
                return
            side = order.get("S")  # "SELL" = Thanh lý LONG | "BUY" = Thanh lý SHORT
            qty = float(order.get("q", 0))
            price = float(order.get("ap", 0)) or float(order.get("p", 0))
            usd_val = qty * price
            now = time.time()

            liq_type = "LONG" if side == "SELL" else "SHORT"
            with self.lock:
                self.events.append((now, sym, liq_type, usd_val))
                self._prune_locked(now)
        except Exception as e:
            logger.debug(f"Lỗi parse liquidation WS: {e}")

    def _prune_locked(self, now):
        cutoff = now - 900  # Giữ dữ liệu 15 phút gần nhất
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def get_stats_15m(self, symbol: str):
        now = time.time()
        long_liq = 0.0
        short_liq = 0.0
        with self.lock:
            self._prune_locked(now)
            for ts, sym, liq_type, usd_val in self.events:
                if sym == symbol:
                    if liq_type == "LONG":
                        long_liq += usd_val
                    else:
                        short_liq += usd_val
        return long_liq, short_liq

    def start(self):
        def run():
            while True:
                try:
                    ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_message=self._on_message,
                        on_error=lambda ws, err: logger.warning(f"WS Error: {err}"),
                        on_close=lambda ws, c, m: logger.info("WS đóng, đang kết nối lại..."),
                    )
                    ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    logger.error(f"WS Crash: {e}")
                time.sleep(5)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        logger.info("Đã khởi động luồng WebSocket theo dõi thanh lý Binance.")


# ==========================================
# 4. THU THẬP DỮ LIỆU ĐA SÀN (NẾN & OI USD)
# ==========================================
def fetch_binance_klines(symbol: str, interval: str, limit: int = 150) -> pd.DataFrame:
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()
    df = pd.DataFrame(
        raw,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
        ],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def fetch_aggregated_oi_usd(symbol: str, ref_price: float) -> dict:
    """Gom tổng Open Interest quy đổi USD từ Binance + Bybit + OKX"""
    oi_data = {"binance": 0.0, "bybit": 0.0, "okx": 0.0, "total": 0.0}

    # 1. Binance Futures (trả về số coin -> nhân giá ra USD)
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=6,
        ).json()
        oi_data["binance"] = float(r.get("openInterest", 0)) * ref_price
    except Exception as e:
        logger.debug(f"Lỗi OI Binance ({symbol}): {e}")

    # 2. Bybit Linear Perpetual (trả về số coin -> nhân giá ra USD)
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 1},
            timeout=6,
        ).json()
        lst = r.get("result", {}).get("list", [])
        if lst:
            oi_data["bybit"] = float(lst[0].get("openInterest", 0)) * ref_price
    except Exception as e:
        logger.debug(f"Lỗi OI Bybit ({symbol}): {e}")

    # 3. OKX USDT-SWAP (ưu tiên trường oiUsd hoặc oiCcy * ref_price)
    try:
        base = symbol.replace("USDT", "")
        inst_id = f"{base}-USDT-SWAP"
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": inst_id},
            timeout=6,
        ).json()
        data = r.get("data", [])
        if data:
            if data[0].get("oiUsd"):
                oi_data["okx"] = float(data[0]["oiUsd"])
            else:
                oi_data["okx"] = float(data[0].get("oiCcy", 0)) * ref_price
    except Exception as e:
        logger.debug(f"Lỗi OI OKX ({symbol}): {e}")

    oi_data["total"] = oi_data["binance"] + oi_data["bybit"] + oi_data["okx"]
    return oi_data


def calculate_poc(df: pd.DataFrame, bins: int = 40) -> float:
    """Tính đường POC (Point of Control) tương tự Fixed Range Volume Profile"""
    low_min = df["low"].min()
    high_max = df["high"].max()
    if high_max <= low_min:
        return float(df["close"].iloc[-1])

    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    hist, bin_edges = np.histogram(typical_price, bins=bins, range=(low_min, high_max), weights=df["volume"])
    max_idx = int(np.argmax(hist))
    return float((bin_edges[max_idx] + bin_edges[max_idx + 1]) / 2.0)


def fmt_usd(val: float) -> str:
    if val >= 1e9:
        return f"${val / 1e9:.2f}B"
    if val >= 1e6:
        return f"${val / 1e6:.2f}M"
    if val >= 1e3:
        return f"${val / 1e3:.1f}K"
    return f"${val:.0f}"


def can_send_alert(key: str) -> bool:
    now = time.time()
    if now - last_alert_time.get(key, 0) >= ALERT_COOLDOWN_SEC:
        last_alert_time[key] = now
        return True
    return False


# ==========================================
# 5. ĐỘNG CƠ PHÂN TÍCH WYCKOFF 2.0
# ==========================================
def analyze_symbol(symbol: str, liq_tracker: LiquidationTracker, manual_check: bool = False):
    df_15m = fetch_binance_klines(symbol, "15m", limit=150)
    df_1h = fetch_binance_klines(symbol, "1h", limit=150)

    # Tính các đường EMA & Volume MA
    df_15m["vol_ma20"] = df_15m["volume"].rolling(20).mean()
    df_15m["ema20"] = df_15m["close"].ewm(span=20, adjust=False).mean()
    df_1h["ema20"] = df_1h["close"].ewm(span=20, adjust=False).mean()
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()

    poc_15m = calculate_poc(df_15m, bins=40)
    poc_1h = calculate_poc(df_1h, bins=40)

    live_price = float(df_15m["close"].iloc[-1])
    closed_15m = df_15m.iloc[-2]  # Nến 15m vừa đóng cửa hoàn chỉnh
    closed_1h = df_1h.iloc[-2]    # Nến 1H vừa đóng cửa hoàn chỉnh
    ema20_1h = float(df_1h["ema20"].iloc[-1])
    ema50_1h = float(df_1h["ema50"].iloc[-1])

    # Thu thập Aggregated OI & tính biến động
    oi_now = fetch_aggregated_oi_usd(symbol, live_price)
    now_ts = time.time()

    with state_lock:
        support = SUPPORT_LEVELS[symbol]
        if oi_now["total"] > 0:
            oi_history[symbol].append((now_ts, oi_now["total"]))
        hist = list(oi_history[symbol])

    oi_change_from_peak_pct = 0.0
    oi_change_15m_pct = 0.0
    if len(hist) >= 2 and oi_now["total"] > 0:
        recent_30m = [v for t, v in hist if now_ts - t <= 1800]
        if recent_30m:
            peak_oi = max(recent_30m)
            oi_change_from_peak_pct = ((oi_now["total"] - peak_oi) / peak_oi) * 100.0
        # Lấy mẫu gần mốc 15 phút trước nhất
        older_15m = [v for t, v in hist if now_ts - t >= 900]
        base_15m = older_15m[-1] if older_15m else hist[0][1]
        oi_change_15m_pct = ((oi_now["total"] - base_15m) / base_15m) * 100.0

    long_liq_15m, short_liq_15m = liq_tracker.get_stats_15m(symbol)

    # Nếu người dùng gọi lệnh /status trên Telegram -> trả báo cáo tổng quan
    if manual_check:
        return (
            f"📊 <b>{symbol} — TRẠNG THÁI WYCKOFF 2.0</b>\n"
            f"• Giá hiện tại: <b>${live_price:,.2f}</b>\n"
            f"• Hỗ trợ đáy (theo dõi): <b>${support:,.2f}</b>\n"
            f"• EMA 20 (1H): <b>${ema20_1h:,.2f}</b> | EMA 50 (1H): <b>${ema50_1h:,.2f}</b>\n"
            f"• POC (15m / 1H): <b>${poc_15m:,.2f}</b> / <b>${poc_1h:,.2f}</b>\n"
            f"• Aggregated OI: <b>{fmt_usd(oi_now['total'])}</b> "
            f"(15m: <code>{oi_change_15m_pct:+.2f}%</code> | Đỉnh 30m: <code>{oi_change_from_peak_pct:+.2f}%</code>)\n"
            f"  └ Binance: {fmt_usd(oi_now['binance'])} | Bybit: {fmt_usd(oi_now['bybit'])} | OKX: {fmt_usd(oi_now['okx'])}\n"
            f"• Thanh lý 15m (Binance): 🔴 Long <b>{fmt_usd(long_liq_15m)}</b> | 🟢 Short <b>{fmt_usd(short_liq_15m)}</b>"
        )

    # Tính thông số nến 15m vừa đóng cửa
    candle_range = closed_15m["high"] - closed_15m["low"]
    lower_wick = min(closed_15m["open"], closed_15m["close"]) - closed_15m["low"]
    wick_ratio = (lower_wick / candle_range) if candle_range > 0 else 0.0
    vol_ratio = (closed_15m["volume"] / closed_15m["vol_ma20"]) if closed_15m["vol_ma20"] > 0 else 0.0

    # --- KỊCH BẢN 1: SPRING / LIQUIDITY SWEEP TẠI ĐÁY HỖ TRỢ ---
    swept_and_reclaimed = (closed_15m["low"] <= support * 1.001) and (closed_15m["close"] > support)
    is_pinbar = wick_ratio >= MIN_WICK_RATIO
    is_vol_spike = vol_ratio >= VOL_SPIKE_MULTIPLIER

    if swept_and_reclaimed and is_pinbar and is_vol_spike:
        if can_send_alert(f"{symbol}_SPRING"):
            cond1 = "✅" if oi_change_from_peak_pct <= OI_DROP_PCT_THRESHOLD else "⚠️ (Chưa xả mạnh)"
            cond2 = "✅"
            cond3 = "✅" if long_liq_15m >= LIQ_SPIKE_THRESHOLD_USD.get(symbol, 500_000) else "⚠️ (Thấp)"

            entry = closed_15m["close"]
            sl = closed_15m["low"] * 0.9985
            risk = max(entry - sl, entry * 0.002)
            tp1 = max(ema20_1h, entry + risk * 1.8)
            tp2 = max(poc_15m, entry + risk * 2.5)
            rr = (tp2 - entry) / risk

            msg = (
                f"🚨 <b>TÍN HIỆU LONG SPRING / SWEEP — {symbol} (15m)</b>\n"
                f"Giá đã quét qua đáy <b>${support:,.2f}</b> (Low: ${closed_15m['low']:,.2f}) và rút chân đóng trên hỗ trợ!\n\n"
                f"<b>Kiểm tra 3 điều kiện cạn cung:</b>\n"
                f"1. OI xả từ đỉnh 30m: {cond1} (<code>{oi_change_from_peak_pct:+.2f}%</code> | Tổng: {fmt_usd(oi_now['total'])})\n"
                f"2. Vol nổ + Rút chân: {cond2} (Vol <b>x{vol_ratio:.1f}</b> | Râu dưới <b>{wick_ratio*100:.0f}%</b>)\n"
                f"3. Thanh lý Long 15m: {cond3} (<b>{fmt_usd(long_liq_15m)}</b>)\n\n"
                f"<b>Gợi ý thiết lập lệnh:</b>\n"
                f"• Entry: <b>${entry:,.2f}</b>\n"
                f"• Stop-Loss: <b>${sl:,.2f}</b>\n"
                f"• TP1 (EMA 20 1H): <b>${tp1:,.2f}</b>\n"
                f"• TP2 (POC): <b>${tp2:,.2f}</b> (R:R ≈ <b>1 : {rr:.1f}</b>)"
            )
            send_telegram(msg)

    # --- KỊCH BẢN 2: BREAK & RETEST EMA 20 KHUNG 1H (SOS CỤC BỘ) ---
    broke_ema20_1h = closed_1h["close"] > ema20_1h * 1.0015
    retest_ema20 = (
        closed_15m["low"] <= ema20_1h * 1.0025
        and closed_15m["close"] > ema20_1h
        and closed_15m["close"] > closed_15m["open"]
    )
    if broke_ema20_1h and retest_ema20:
        if can_send_alert(f"{symbol}_EMA20_RETEST"):
            entry = closed_15m["close"]
            sl = min(closed_15m["low"] * 0.997, support * 0.999)
            risk = max(entry - sl, entry * 0.003)
            tp1 = max(poc_15m, entry + risk * 1.5)
            tp2 = max(ema50_1h, poc_1h, entry + risk * 2.2)

            msg = (
                f"🟢 <b>TÍN HIỆU BREAK & RETEST EMA 20 (1H) — {symbol}</b>\n"
                f"Nến 1H đã đóng vượt <b>EMA 20 (${ema20_1h:,.2f})</b> và nến 15m vừa Retest bật xanh!\n\n"
                f"• Aggregated OI: <b>{fmt_usd(oi_now['total'])}</b> (15m: <code>{oi_change_15m_pct:+.2f}%</code>)\n"
                f"• Entry tham khảo: <b>${entry:,.2f}</b>\n"
                f"• Stop-Loss: <b>${sl:,.2f}</b>\n"
                f"• TP1: <b>${tp1:,.2f}</b> | TP2: <b>${tp2:,.2f}</b>"
            )
            send_telegram(msg)

    # --- KỊCH BẢN 3: CẢNH BÁO PHÁ ĐÁY + OI TĂNG (PHE SHORT NHỒI LỆNH) ---
    hard_breakdown = closed_15m["close"] < support * 0.998
    oi_surging = oi_change_15m_pct >= OI_SURGE_PCT_THRESHOLD

    if hard_breakdown and oi_surging:
        if can_send_alert(f"{symbol}_BREAKDOWN_OI"):
            msg = (
                f"⛔ <b>CẢNH BÁO PHÁ ĐÁY + OI TĂNG — {symbol}</b>\n"
                f"Nến 15m đóng đặc dưới hỗ trợ <b>${support:,.2f}</b> (Close: <b>${closed_15m['close']:,.2f}</b>) "
                f"kèm Aggregated OI tăng <code>+{oi_change_15m_pct:.2f}%</code> ({fmt_usd(oi_now['total'])}).\n\n"
                f"⚠️ <b>Hành động:</b> Phe Short đang chủ động nhồi lệnh phá cấu trúc. "
                f"<b>Tuyệt đối không bắt dao Long!</b> Đợi giá rơi về vùng HVN bên dưới và OI sập mạnh."
            )
            send_telegram(msg)


# ==========================================
# 6. LUỒNG NGHE LỆNH TELEGRAM TỪ ĐIỆN THOẠI
# ==========================================
def telegram_command_listener(liq_tracker: LiquidationTracker):
    if not TELEGRAM_TOKEN:
        return
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    logger.info("Đã bật trình lắng nghe lệnh Telegram (/status, /support, /help).")

    while True:
        try:
            resp = requests.get(url, params={"offset": offset, "timeout": 25}, timeout=30).json()
            for item in resp.get("result", []):
                offset = item["update_id"] + 1
                msg = item.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text.startswith("/status"):
                    reports = []
                    for sym in list(SUPPORT_LEVELS.keys()):
                        reports.append(analyze_symbol(sym, liq_tracker, manual_check=True))
                    send_telegram("\n\n".join(reports))

                elif text.startswith("/support"):
                    parts = text.split()
                    if len(parts) == 3:
                        sym = parts[1].upper()
                        try:
                            new_lvl = float(parts[2])
                            with state_lock:
                                SUPPORT_LEVELS[sym] = new_lvl
                                if sym not in oi_history:
                                    oi_history[sym] = deque(maxlen=60)
                            send_telegram(f"✅ Đã cập nhật mốc hỗ trợ <b>{sym}</b> thành <b>${new_lvl:,.2f}</b>.")
                        except ValueError:
                            send_telegram("❌ Sai định dạng giá. Ví dụ đúng: <code>/support BTCUSDT 62006</code>")
                    else:
                        send_telegram("ℹ️ Cú pháp: <code>/support BTCUSDT 62006</code>")

                elif text.startswith("/help"):
                    send_telegram(
                        "🤖 <b>Lệnh điều khiển Wyckoff Scanner:</b>\n"
                        "• <code>/status</code> — Xem giá, EMA 20 1H, POC, Aggregated OI & Thanh lý 15m.\n"
                        "• <code>/support BTCUSDT 62006</code> — Cập nhật mốc hỗ trợ đáy đôi cần canh.\n"
                        "• Bot tự động quét 24/7 mỗi 60 giây và báo ngay khi đủ điều kiện."
                    )
        except Exception as e:
            logger.debug(f"Lỗi polling Telegram: {e}")
            time.sleep(5)


# ==========================================
# 7. CHƯƠNG TRÌNH CHÍNH
# ==========================================
def main():
    liq_tracker = LiquidationTracker(SUPPORT_LEVELS.keys())
    liq_tracker.start()

    cmd_thread = threading.Thread(target=telegram_command_listener, args=(liq_tracker,), daemon=True)
    cmd_thread.start()

    send_telegram(
        "🚀 <b>Wyckoff 2.0 OI Scanner đã khởi động!</b>\n"
        f"Đang theo dõi: <code>{', '.join(SUPPORT_LEVELS.keys())}</code>\n"
        "Gõ <code>/status</code> để xem chỉ số ngay hoặc <code>/help</code> để xem hướng dẫn."
    )

    while True:
        start_time = time.time()
        for sym in list(SUPPORT_LEVELS.keys()):
            try:
                analyze_symbol(sym, liq_tracker, manual_check=False)
            except Exception as e:
                logger.error(f"Lỗi phân tích {sym}: {e}")
        elapsed = time.time() - start_time
        time.sleep(max(5, SCAN_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    main()
