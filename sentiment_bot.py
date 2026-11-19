import os
import sys
from datetime import datetime, timezone
import requests
import numpy as np
import pandas as pd

# ==========================================
# 1. CẤU HÌNH THÔNG SỐ & MỐC HỖ TRỢ WYCKOFF 2.0
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID", "")

# Mốc đáy hỗ trợ cứng đang theo dõi (sửa trực tiếp tại đây khi đổi cấu trúc)
SUPPORT_LEVELS = {
    "BTCUSDT": 62006.0,
    "ETHUSDT": 1765.0,
}

# Ngưỡng lọc tín hiệu
MIN_WICK_RATIO = 0.50          # Râu dưới >= 50% tổng chiều dài nến
VOL_SPIKE_MULTIPLIER = 1.6     # Volume >= 1.6 lần trung bình 20 nến
OI_DROP_PCT_THRESHOLD = -0.30  # OI giảm ít nhất 0.30% từ đỉnh 3H gần nhất
OI_SURGE_PCT_THRESHOLD = 0.30  # OI tăng >= +0.30% trong 15m khi phá đáy


# ==========================================
# 2. TIỆN ÍCH TELEGRAM & LẤY IP MÁY CHỦ
# ==========================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Thiếu TELEGRAM_TOKEN hoặc TELEGRAM_CHAT_ID trong Secrets/Env.")
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
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code != 200:
            print(f"Lỗi gửi Telegram: {r.text}")
    except Exception as e:
        print(f"Exception gửi Telegram: {e}")


def get_server_ip() -> str:
    try:
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return "Unknown"


def fmt_usd(val: float) -> str:
    if val >= 1e9:
        return f"${val / 1e9:.2f}B"
    if val >= 1e6:
        return f"${val / 1e6:.2f}M"
    if val >= 1e3:
        return f"${val / 1e3:.1f}K"
    return f"${val:.0f}"


# ==========================================
# 3. LẤY DỮ LIỆU NẾN (TỰ ĐỘNG FALLBACK BYBIT NẾU BINANCE CHẶN IP)
# ==========================================
def fetch_klines(symbol: str, interval: str, limit: int = 150) -> tuple[pd.DataFrame, str]:
    # Ưu tiên 1: Binance Futures
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=8,
        )
        if r.status_code == 200:
            raw = r.json()
            df = pd.DataFrame(
                raw,
                columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
                ],
            )
            for col in ["open", "high", "low", "close", "volume", "tb_base"]:
                df[col] = df[col].astype(float)
            df["taker_sell_vol"] = df["volume"] - df["tb_base"]
            return df, "Binance"
    except Exception:
        pass

    # Ưu tiên 2: Bybit Linear (Không bị chặn IP Mỹ của GitHub Actions)
    bybit_map = {"15m": "15", "1h": "60", "4h": "240"}
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": bybit_map[interval], "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json()["result"]["list"][::-1]  # Đảo ngược từ cũ -> mới
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["taker_sell_vol"] = np.where(df["close"] < df["open"], df["volume"] * 0.6, df["volume"] * 0.4)
    return df, "Bybit"


# ==========================================
# 4. LẤY AGGREGATED OI & LỊCH SỬ OI 3 GIỜ GẦN NHẤT
# ==========================================
def fetch_oi_metrics(symbol: str, ref_price: float) -> dict:
    """Lấy tổng OI đa sàn hiện tại + lịch sử OI 12 nến 15m (3 giờ) từ Bybit/Binance"""
    bybit_oi_now, binance_oi_now, okx_oi_now = 0.0, 0.0, 0.0
    oi_series_usd = []

    # 1. Bybit 15m OI History (12 nến = 3 giờ)
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "15min", "limit": 12},
            timeout=8,
        ).json()
        lst = r.get("result", {}).get("list", [])[::-1]
        if lst:
            oi_series_usd = [float(x["openInterest"]) * ref_price for x in lst]
            bybit_oi_now = oi_series_usd[-1]
    except Exception:
        pass

    # 2. Binance Futures OI hiện tại
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=6,
        )
        if r.status_code == 200:
            binance_oi_now = float(r.json().get("openInterest", 0)) * ref_price
    except Exception:
        pass

    # 3. OKX Swap OI hiện tại
    try:
        inst_id = f"{symbol.replace('USDT', '')}-USDT-SWAP"
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": inst_id},
            timeout=6,
        ).json()
        data = r.get("data", [])
        if data:
            okx_oi_now = float(data[0].get("oiUsd") or float(data[0].get("oiCcy", 0)) * ref_price)
    except Exception:
        pass

    total_oi = binance_oi_now + bybit_oi_now + okx_oi_now
    drop_from_peak_pct = 0.0
    change_15m_pct = 0.0

    if len(oi_series_usd) >= 2 and oi_series_usd[-1] > 0:
        peak_3h = max(oi_series_usd)
        drop_from_peak_pct = ((oi_series_usd[-1] - peak_3h) / peak_3h) * 100.0
        change_15m_pct = ((oi_series_usd[-1] - oi_series_usd[-2]) / oi_series_usd[-2]) * 100.0

    return {
        "total": total_oi,
        "binance": binance_oi_now,
        "bybit": bybit_oi_now,
        "okx": okx_oi_now,
        "drop_from_peak_pct": drop_from_peak_pct,
        "change_15m_pct": change_15m_pct,
    }


def calculate_poc(df: pd.DataFrame, bins: int = 40) -> float:
    low_min, high_max = df["low"].min(), df["high"].max()
    if high_max <= low_min:
        return float(df["close"].iloc[-1])
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    hist, edges = np.histogram(typical, bins=bins, range=(low_min, high_max), weights=df["volume"])
    idx = int(np.argmax(hist))
    return float((edges[idx] + edges[idx + 1]) / 2.0)


# ==========================================
# 5. PHÂN TÍCH ĐƠN LƯỢT (SINGLE-PASS SCANNER)
# ==========================================
def scan_symbol(symbol: str, support: float) -> tuple[list[str], str]:
    df_15m, source = fetch_klines(symbol, "15m", limit=150)
    df_1h, _ = fetch_klines(symbol, "1h", limit=150)

    df_15m["vol_ma20"] = df_15m["volume"].rolling(20).mean()
    df_15m["sell_ma20"] = df_15m["taker_sell_vol"].rolling(20).mean()
    df_1h["ema20"] = df_1h["close"].ewm(span=20, adjust=False).mean()
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()

    poc_15m = calculate_poc(df_15m)
    poc_1h = calculate_poc(df_1h)

    live_price = float(df_15m["close"].iloc[-1])
    closed_15m = df_15m.iloc[-2]  # Nến 15m vừa đóng cửa
    closed_1h = df_1h.iloc[-2]    # Nến 1H vừa đóng cửa
    ema20_1h = float(df_1h["ema20"].iloc[-1])
    ema50_1h = float(df_1h["ema50"].iloc[-1])

    oi = fetch_oi_metrics(symbol, live_price)

    # Tính các mốc kích hoạt thanh lý (Trigger Zones) từ giá hiện tại
    liq_50x = live_price * 0.985
    liq_20x = live_price * 0.955
    liq_10x = live_price * 0.905

    # Thông số nến 15m vừa đóng
    candle_range = closed_15m["high"] - closed_15m["low"]
    lower_wick = min(closed_15m["open"], closed_15m["close"]) - closed_15m["low"]
    wick_ratio = (lower_wick / candle_range) if candle_range > 0 else 0.0
    vol_ratio = (closed_15m["volume"] / closed_15m["vol_ma20"]) if closed_15m["vol_ma20"] > 0 else 0.0
    sell_spike = closed_15m["taker_sell_vol"] >= (closed_15m["sell_ma20"] * 1.5)

    alerts = []

    # KỊCH BẢN 1: SPRING / LIQUIDITY SWEEP TẠI ĐÁY HỖ TRỢ
    swept_reclaimed = (closed_15m["low"] <= support * 1.001) and (closed_15m["close"] > support)
    if swept_reclaimed and wick_ratio >= MIN_WICK_RATIO and vol_ratio >= VOL_SPIKE_MULTIPLIER:
        c1 = "✅" if oi["drop_from_peak_pct"] <= OI_DROP_PCT_THRESHOLD else "⚠️"
        c2 = "✅"
        c3 = "✅" if sell_spike else "⚠️"

        entry = closed_15m["close"]
        sl = closed_15m["low"] * 0.9985
        risk = max(entry - sl, entry * 0.002)
        tp1 = max(ema20_1h, entry + risk * 1.8)
        tp2 = max(poc_15m, entry + risk * 2.5)
        rr = (tp2 - entry) / risk

        alerts.append(
            f"🚨 <b>TÍN HIỆU LONG SPRING — {symbol} (15m)</b>\n"
            f"Quét đáy <b>${support:,.2f}</b> (Low: ${closed_15m['low']:,.2f}) & rút chân đóng trên hỗ trợ!\n"
            f"1. OI xả từ đỉnh 3H: {c1} (<code>{oi['drop_from_peak_pct']:+.2f}%</code>)\n"
            f"2. Vol nổ + Rút chân: {c2} (Vol <b>x{vol_ratio:.1f}</b> | Râu <b>{wick_ratio*100:.0f}%</b>)\n"
            f"3. Áp lực bán giải chấp: {c3}\n"
            f"👉 <b>Entry:</b> ${entry:,.2f} | <b>SL:</b> ${sl:,.2f}\n"
            f"🎯 <b>TP1:</b> ${tp1:,.2f} | <b>TP2:</b> ${tp2:,.2f} (R:R <b>1:{rr:.1f}</b>)"
        )

    # KỊCH BẢN 2: BREAK & RETEST EMA 20 KHUNG 1H
    broke_ema20_1h = closed_1h["close"] > ema20_1h * 1.0015
    retest_ema20 = (
        closed_15m["low"] <= ema20_1h * 1.0025
        and closed_15m["close"] > ema20_1h
        and closed_15m["close"] > closed_15m["open"]
    )
    if broke_ema20_1h and retest_ema20:
        entry = closed_15m["close"]
        sl = min(closed_15m["low"] * 0.997, support * 0.999)
        risk = max(entry - sl, entry * 0.003)
        tp1 = max(poc_15m, entry + risk * 1.5)
        tp2 = max(ema50_1h, poc_1h, entry + risk * 2.2)

        alerts.append(
            f"🟢 <b>BREAK & RETEST EMA 20 (1H) — {symbol}</b>\n"
            f"Nến 1H đóng vượt <b>EMA 20 (${ema20_1h:,.2f})</b> và nến 15m vừa Retest bật xanh!\n"
            f"👉 <b>Entry:</b> ${entry:,.2f} | <b>SL:</b> ${sl:,.2f}\n"
            f"🎯 <b>TP1:</b> ${tp1:,.2f} | <b>TP2:</b> ${tp2:,.2f}"
        )

    # KỊCH BẢN 3: CẢNH BÁO PHÁ ĐÁY + OI TĂNG
    if closed_15m["close"] < support * 0.998 and oi["change_15m_pct"] >= OI_SURGE_PCT_THRESHOLD:
        alerts.append(
            f"⛔ <b>CẢNH BÁO PHÁ ĐÁY + OI TĂNG — {symbol}</b>\n"
            f"Nến 15m đóng đặc dưới hỗ trợ <b>${support:,.2f}</b> (Close: ${closed_15m['close']:,.2f}) "
            f"kèm OI tăng <code>+{oi['change_15m_pct']:.2f}%</code>. <b>Không bắt dao Long!</b>"
        )

    # Bảng tóm tắt định kỳ (dùng cho nhịp báo cáo 4H)
    summary = (
        f"📊 <b>{symbol}</b> ({source}): <b>${live_price:,.2f}</b>\n"
        f"• Đáy canh: <b>${support:,.2f}</b> | EMA20 (1H): <b>${ema20_1h:,.2f}</b>\n"
        f"• POC (15m/1H): ${poc_15m:,.2f} / ${poc_1h:,.2f}\n"
        f"• Agg OI: <b>{fmt_usd(oi['total'])}</b> (15m: <code>{oi['change_15m_pct']:+.2f}%</code> | Đỉnh 3H: <code>{oi['drop_from_peak_pct']:+.2f}%</code>)\n"
        f"• Trigger Zones: 50x <code>${liq_50x:,.0f}</code> | 20x <code>${liq_20x:,.0f}</code> | 10x <code>${liq_10x:,.0f}</code>"
    )
    return alerts, summary


def main():
    now_utc = datetime.now(timezone.utc)
    server_ip = get_server_ip()
    all_alerts = []
    all_summaries = []

    for sym, sup in SUPPORT_LEVELS.items():
        try:
            alerts, summary = scan_symbol(sym, sup)
            all_alerts.extend(alerts)
            all_summaries.append(summary)
        except Exception as e:
            print(f"Lỗi quét {sym}: {e}")

    # 1. Nếu có tín hiệu Entry hoặc Cảnh báo phá đáy -> Bắn Telegram ngay lập tức
    if all_alerts:
        body = "\n\n".join(all_alerts) + f"\n\n🖥 <i>Server IP: {server_ip}</i>"
        send_telegram(body)

    # 2. Định kỳ mỗi 4 tiếng (00h, 04h, 08h, 12h, 16h, 20h UTC) -> Gửi bản tin tổng hợp & lộ diện IP
    is_4h_heartbeat = (now_utc.hour % 4 == 0) and (now_utc.minute < 15)
    if is_4h_heartbeat or os.getenv("FORCE_REPORT") == "1":
        header = f"🧭 <b>WYCKOFF 2.0 SCANNER — BÁO CÁO 4H ({now_utc.strftime('%H:%M')} UTC)</b>\n\n"
        footer = f"\n\n🖥 <i>Server IP: {server_ip}</i>"
        send_telegram(header + "\n\n".join(all_summaries) + footer)

    print("Hoàn tất lượt quét.")
    sys.exit(0)


if __name__ == "__main__":
    main()
