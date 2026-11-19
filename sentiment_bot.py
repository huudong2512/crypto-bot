import os
import sys
import time
from datetime import datetime, timezone, timedelta
import requests
import numpy as np
import pandas as pd

# ==========================================
# 1. CẤU HÌNH THÔNG SỐ & MỐC HỖ TRỢ WYCKOFF 2.0
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID", "")

SUPPORT_LEVELS = {
    "BTCUSDT": 62006.0,
    "ETHUSDT": 1765.0,
}

MIN_WICK_RATIO = 0.50          # Râu dưới >= 50% chiều dài cây nến
VOL_SPIKE_MULTIPLIER = 1.6     # Volume >= 1.6x trung bình 20 nến
OI_DROP_PCT_THRESHOLD = -0.30  # OI giảm >= 0.30% so với đỉnh 3H
OI_SURGE_PCT_THRESHOLD = 0.30  # OI tăng >= +0.30% trong 15m khi phá đáy


# ==========================================
# 2. TIỆN ÍCH TELEGRAM, IP & ĐỌC LỆNH /STATUS
# ==========================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Thiếu TELEGRAM_TOKEN hoặc TELEGRAM_CHAT_ID trong Secrets.")
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


def check_status_command() -> bool:
    """Đọc xem bạn có vừa nhắn /status không để trả đủ cả 3 khung 15M+1H+4H"""
    if not TELEGRAM_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    asked = False
    try:
        resp = requests.get(url, params={"timeout": 1}, timeout=6).json()
        updates = resp.get("result", [])
        if updates:
            for item in updates:
                msg = item.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip().lower()
                if (not TELEGRAM_CHAT_ID or chat_id == str(TELEGRAM_CHAT_ID)) and text.startswith("/status"):
                    asked = True
            last_id = updates[-1]["update_id"]
            requests.get(url, params={"offset": last_id + 1, "timeout": 1}, timeout=5)
    except Exception:
        pass
    return asked


def fmt_usd(val: float) -> str:
    if val >= 1e9:
        return f"${val / 1e9:.2f}B"
    if val >= 1e6:
        return f"${val / 1e6:.2f}M"
    if val >= 1e3:
        return f"${val / 1e3:.1f}K"
    return f"${val:.0f}"


def describe_candle(row: pd.Series) -> tuple[str, float, float]:
    is_green = row["close"] >= row["open"]
    icon = "🟢" if is_green else "🔴"
    c_range = row["high"] - row["low"]
    lower_wick = min(row["open"], row["close"]) - row["low"]
    wick_ratio = (lower_wick / c_range) if c_range > 0 else 0.0
    vol_ratio = (row["volume"] / row["vol_ma20"]) if row["vol_ma20"] > 0 else 0.0
    desc = f"{icon} Close <b>${row['close']:,.2f}</b> | Vol <b>x{vol_ratio:.1f}</b> | Râu dưới <b>{wick_ratio*100:.0f}%</b>"
    return desc, wick_ratio, vol_ratio


# ==========================================
# 3. LẤY NẾN ĐA SÀN (BINANCE -> OKX -> HYPERLIQUID)
# ==========================================
def fetch_klines(symbol: str, interval: str, limit: int = 150) -> tuple[pd.DataFrame, str]:
    base = symbol.replace("USDT", "")
    errors = []

    # Nguồn 1: Binance Futures (Chạy mượt nếu dùng VPS Singapore/VN)
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5,
        )
        if r.status_code == 200:
            df = pd.DataFrame(
                r.json(),
                columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
                ],
            )
            for col in ["open", "high", "low", "close", "volume", "tb_base"]:
                df[col] = df[col].astype(float)
            df["taker_sell_vol"] = df["volume"] - df["tb_base"]
            return df, "Binance"
        errors.append(f"Binance:{r.status_code}")
    except Exception as e:
        errors.append(f"Binance:{type(e).__name__}")

    # Nguồn 2: OKX Perpetual Swap (Không bị chặn trên GitHub Actions)
    try:
        okx_bar = {"15m": "15m", "1h": "1H", "4h": "4H"}[interval]
        r = requests.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": f"{base}-USDT-SWAP", "bar": okx_bar, "limit": str(min(limit, 300))},
            timeout=8,
        )
        if r.status_code == 200:
            payload = r.json()
            if payload.get("code") == "0" and payload.get("data"):
                rows = payload["data"][::-1]  # Đảo ngược từ cũ -> mới
                df = pd.DataFrame(
                    rows,
                    columns=["open_time", "open", "high", "low", "close", "vol_ct", "volume", "vol_quote", "confirm"],
                )
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                df["taker_sell_vol"] = np.where(df["close"] < df["open"], df["volume"] * 0.6, df["volume"] * 0.4)
                return df, "OKX"
        errors.append(f"OKX:{r.status_code}")
    except Exception as e:
        errors.append(f"OKX:{type(e).__name__}")

    # Nguồn 3: Hyperliquid Perp (Không bao giờ chặn IP Cloud)
    try:
        sec_map = {"15m": 900, "1h": 3600, "4h": 14400}
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (limit * sec_map[interval] * 1000)
        r = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "candleSnapshot", "req": {"coin": base, "interval": interval, "startTime": start_ms, "endTime": end_ms}},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            rows = r.json()
            df = pd.DataFrame([
                {
                    "open_time": int(x["t"]),
                    "open": float(x["o"]),
                    "high": float(x["h"]),
                    "low": float(x["l"]),
                    "close": float(x["c"]),
                    "volume": float(x["v"]),
                }
                for x in rows
            ])
            df["taker_sell_vol"] = np.where(df["close"] < df["open"], df["volume"] * 0.6, df["volume"] * 0.4)
            return df, "Hyperliquid"
        errors.append(f"HL:{r.status_code}")
    except Exception as e:
        errors.append(f"HL:{type(e).__name__}")

    raise RuntimeError(f"Mọi sàn đều lỗi ({', '.join(errors)})")


# ==========================================
# 4. LẤY AGGREGATED OI & LỊCH SỬ OI 3 GIỜ
# ==========================================
def fetch_oi_metrics(symbol: str, ref_price: float) -> dict:
    base = symbol.replace("USDT", "")
    inst_id = f"{base}-USDT-SWAP"
    oi_sources = {}
    oi_series = []

    # 1. OKX OI hiện tại + Lịch sử OI 15m (12 nến = 3 giờ)
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history",
            params={"instId": inst_id, "period": "15m", "limit": "12"},
            timeout=8,
        ).json()
        if r.get("code") == "0" and r.get("data"):
            # Định dạng OKX: [ts, oi, oiCcy, oiUsd]
            rows = r["data"][::-1]
            oi_series = [float(x[3]) for x in rows if len(x) >= 4 and float(x[3]) > 0]
            if oi_series:
                oi_sources["OKX"] = oi_series[-1]
    except Exception:
        pass

    if "OKX" not in oi_sources:
        try:
            r = requests.get(
                "https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": inst_id},
                timeout=6,
            ).json()
            data = r.get("data", [])
            if data:
                val = float(data[0].get("oiUsd") or float(data[0].get("oiCcy", 0)) * ref_price)
                if val > 0:
                    oi_sources["OKX"] = val
        except Exception:
            pass

    # 2. Hyperliquid OI hiện tại
    try:
        r = requests.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=8).json()
        if isinstance(r, list) and len(r) == 2:
            for asset, ctx in zip(r[0].get("universe", []), r[1]):
                if asset.get("name") == base:
                    oi_hl = float(ctx.get("openInterest", 0)) * float(ctx.get("markPx", ref_price))
                    if oi_hl > 0:
                        oi_sources["HL"] = oi_hl
                    break
    except Exception:
        pass

    # 3. Binance OI (Chỉ tính nếu IP không bị chặn)
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": symbol}, timeout=4)
        if r.status_code == 200:
            val = float(r.json().get("openInterest", 0)) * ref_price
            if val > 0:
                oi_sources["BN"] = val
    except Exception:
        pass

    # 4. Bybit OI (Chỉ tính nếu IP không bị chặn)
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": "15min", "limit": 12},
            timeout=4,
        )
        if r.status_code == 200:
            lst = r.json().get("result", {}).get("list", [])[::-1]
            if lst:
                bb_series = [float(x["openInterest"]) * ref_price for x in lst]
                oi_sources["BB"] = bb_series[-1]
                if not oi_series:
                    oi_series = bb_series
    except Exception:
        pass

    total_oi = sum(oi_sources.values())
    drop_from_peak = 0.0
    change_15m = 0.0
    if len(oi_series) >= 2 and oi_series[-2] > 0:
        peak_3h = max(oi_series)
        drop_from_peak = ((oi_series[-1] - peak_3h) / peak_3h) * 100.0
        change_15m = ((oi_series[-1] - oi_series[-2]) / oi_series[-2]) * 100.0

    src_label = "+".join(oi_sources.keys()) if oi_sources else "N/A"
    return {
        "total": total_oi,
        "sources": src_label,
        "drop_from_peak_pct": drop_from_peak,
        "change_15m_pct": change_15m,
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
# 5. ĐỘNG CƠ BÁO CÁO ĐA KHUNG (15M / 1H / 4H)
# ==========================================
def scan_symbol(symbol: str, support: float, include_1h: bool, include_4h: bool) -> tuple[list[str], str]:
    df_15m, source = fetch_klines(symbol, "15m", limit=150)
    df_1h, _ = fetch_klines(symbol, "1h", limit=150)

    df_15m["vol_ma20"] = df_15m["volume"].rolling(20).mean()
    df_15m["sell_ma20"] = df_15m["taker_sell_vol"].rolling(20).mean()
    df_1h["vol_ma20"] = df_1h["volume"].rolling(20).mean()
    df_1h["ema20"] = df_1h["close"].ewm(span=20, adjust=False).mean()
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()

    poc_15m = calculate_poc(df_15m)
    poc_1h = calculate_poc(df_1h)

    live_price = float(df_15m["close"].iloc[-1])
    closed_15m = df_15m.iloc[-2]
    closed_1h = df_1h.iloc[-2]
    ema20_1h = float(df_1h["ema20"].iloc[-1])
    ema50_1h = float(df_1h["ema50"].iloc[-1])

    oi = fetch_oi_metrics(symbol, live_price)
    desc_15m, wick_15m, vol_15m = describe_candle(closed_15m)
    sell_spike = closed_15m["taker_sell_vol"] >= (closed_15m["sell_ma20"] * 1.5)

    alerts = []

    # 1. Tín hiệu Spring / Liquidity Sweep (15m)
    swept_reclaimed = (closed_15m["low"] <= support * 1.001) and (closed_15m["close"] > support)
    if swept_reclaimed and wick_15m >= MIN_WICK_RATIO and vol_15m >= VOL_SPIKE_MULTIPLIER:
        entry = closed_15m["close"]
        sl = closed_15m["low"] * 0.9985
        risk = max(entry - sl, entry * 0.002)
        tp1 = max(ema20_1h, entry + risk * 1.8)
        tp2 = max(poc_15m, entry + risk * 2.5)
        alerts.append(
            f"🚨 <b>LONG SPRING — {symbol} (15m)</b>\n"
            f"Quét đáy ${support:,.2f} (Low: ${closed_15m['low']:,.2f}) & rút chân!\n"
            f"Entry: <b>${entry:,.2f}</b> | SL: <b>${sl:,.2f}</b> | TP1: ${tp1:,.2f} | TP2: ${tp2:,.2f}"
        )

    # 2. Tín hiệu Break & Retest EMA 20 (1H)
    if (
        closed_1h["close"] > ema20_1h * 1.0015
        and closed_15m["low"] <= ema20_1h * 1.0025
        and closed_15m["close"] > ema20_1h
        and closed_15m["close"] > closed_15m["open"]
    ):
        entry = closed_15m["close"]
        sl = min(closed_15m["low"] * 0.997, support * 0.999)
        alerts.append(
            f"🟢 <b>BREAK & RETEST EMA20 1H — {symbol}</b>\n"
            f"Entry: <b>${entry:,.2f}</b> | SL: <b>${sl:,.2f}</b> | TP1: ${poc_15m:,.2f}"
        )

    # 3. Cảnh báo phá đáy + OI tăng
    if closed_15m["close"] < support * 0.998 and oi["change_15m_pct"] >= OI_SURGE_PCT_THRESHOLD:
        alerts.append(
            f"⛔ <b>PHÁ ĐÁY + OI TĂNG — {symbol}</b>\n"
            f"Đóng 15m dưới ${support:,.2f} (${closed_15m['close']:,.2f}) kèm OI <code>+{oi['change_15m_pct']:.2f}%</code>. Không bắt dao!"
        )

    # Khối báo cáo cố định 15m
    lines = [
        f"📊 <b>{symbol}</b> ({source}) | Đáy canh: <b>${support:,.2f}</b>",
        f"• <b>[15m]</b> {desc_15m}",
        f"• EMA20 (1H): <b>${ema20_1h:,.2f}</b> | POC 15m: ${poc_15m:,.2f}",
        f"• OI ({oi['sources']}): <b>{fmt_usd(oi['total'])}</b> (15m: <code>{oi['change_15m_pct']:+.2f}%</code> | Đỉnh 3H: <code>{oi['drop_from_peak_pct']:+.2f}%</code>)",
        f"• Trigger (50x/20x/10x): <code>${live_price*0.985:,.0f}</code> / <code>${live_price*0.955:,.0f}</code> / <code>${live_price*0.905:,.0f}</code>",
    ]

    # Khối nối thêm khi đóng nến 1H
    if include_1h:
        desc_1h, _, _ = describe_candle(closed_1h)
        pos_1h = "TRÊN EMA20 ✅" if closed_1h["close"] > ema20_1h else "DƯỚI EMA20 ⚠️"
        lines.append(f"⏱ <b>[ĐÓNG NẾN 1H]</b> {desc_1h}")
        lines.append(f"  └ Cấu trúc 1H: <b>{pos_1h}</b> | EMA50: ${ema50_1h:,.2f} | POC 1H: ${poc_1h:,.2f}")

    # Khối nối thêm khi đóng nến 4H
    if include_4h:
        df_4h, _ = fetch_klines(symbol, "4h", limit=150)
        df_4h["vol_ma20"] = df_4h["volume"].rolling(20).mean()
        df_4h["ema20"] = df_4h["close"].ewm(span=20, adjust=False).mean()
        closed_4h = df_4h.iloc[-2]
        ema20_4h = float(df_4h["ema20"].iloc[-1])
        poc_4h = calculate_poc(df_4h)
        desc_4h, wick_4h, vol_4h = describe_candle(closed_4h)

        c1 = "✅" if oi["drop_from_peak_pct"] <= OI_DROP_PCT_THRESHOLD else "⚠️"
        c2 = "✅" if (wick_4h >= 0.45 and vol_4h >= 1.3) else "⚠️"
        c3 = "✅" if sell_spike else "⚠️"

        lines.append(f"🔥 <b>[ĐÓNG NẾN 4H]</b> {desc_4h}")
        lines.append(f"  └ EMA20 4H: <b>${ema20_4h:,.2f}</b> | POC 4H: <b>${poc_4h:,.2f}</b>")
        lines.append(f"  └ 3 ĐK Wyckoff: OI xả {c1} | Vol+Râu {c2} | Quét bán {c3}")

    return alerts, "\n".join(lines)


def main():
    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc + timedelta(hours=7)
    server_ip = get_server_ip()
    user_asked_status = check_status_command()
    force_all = (os.getenv("FORCE_ALL_TF") == "1") or user_asked_status

    is_1h_close = (now_utc.minute < 15) or force_all
    is_4h_close = (now_utc.hour % 4 == 0 and now_utc.minute < 15) or force_all

    if is_4h_close:
        tf_badge = "15M + 1H + 4H 🔥"
    elif is_1h_close:
        tf_badge = "15M + 1H ⏱"
    else:
        tf_badge = "15M"

    all_alerts = []
    all_reports = []

    for sym, sup in SUPPORT_LEVELS.items():
        try:
            alerts, report = scan_symbol(sym, sup, include_1h=is_1h_close, include_4h=is_4h_close)
            all_alerts.extend(alerts)
            all_reports.append(report)
        except Exception as e:
            err_msg = f"⚠️ <b>{sym}</b>: Lỗi kéo dữ liệu ({e})"
            print(err_msg)
            all_reports.append(err_msg)

    header = f"🧭 <b>WYCKOFF SCANNER [{tf_badge}] — {now_vn.strftime('%H:%M')} (VN)</b>\n"
    alert_block = ("\n\n".join(all_alerts) + "\n\n" + "─" * 20 + "\n\n") if all_alerts else ""
    footer = f"\n\n🖥 <i>Server IP: {server_ip}</i>"
    body = header + "\n" + alert_block + "\n\n".join(all_reports) + footer

    send_telegram(body)
    print(f"Đã gửi báo cáo [{tf_badge}] lúc {now_vn.strftime('%H:%M:%S')} VN | IP: {server_ip}")
    sys.exit(0)


if __name__ == "__main__":
    main()
