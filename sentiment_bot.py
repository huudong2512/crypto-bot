import datetime
import requests

# --- CẤU HÌNH THÔNG TIN CÁ NHÂN CỦA BẠN ---
# ⚠️ LƯU Ý: Nếu chạy thật, nên cân nhắc đổi API Key/Token mới vì đã bị lộ trên mạng.
TELEGRAM_BOT_TOKEN = "8810905518:AAH-l70WmQaQ3v1DqlXu7j9JquX0yJfZpOk"
TELEGRAM_CHAT_ID = "413611713"
COINGLASS_API_KEY = "3cf3522543f8449795ea641ec3e4f80d"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        print(f"-> Kết quả gửi Telegram: HTTP {res.status_code}")
    except Exception as e:
        print(f"❌ Lỗi gửi Telegram: {e}")

def get_btc_price_global():
    """Lấy giá BTC từ CoinGecko, fallback sang Binance Spot."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        res = requests.get(url, timeout=10).json()
        return float(res["bitcoin"]["usd"])
    except Exception:
        try:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            res = requests.get(url, timeout=10).json()
            return float(res.get("price", 0))
        except Exception:
            return 0.0

def get_coinglass_data():
    """Kéo dữ liệu từ CoinGlass (Sẽ fail nếu API Key free bị chặn)."""
    headers = {
        "accept": "application/json",
        "CG-API-KEY": COINGLASS_API_KEY,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    funding_rate = 0.0
    long_pct, short_pct = 0.0, 0.0
    buy_vol_btc, sell_vol_btc = 0.0, 0.0

    # 1. FUNDING RATE
    try:
        url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/funding_rates/chart?symbol=BTC&type=0"
        res = requests.get(url, headers=headers, timeout=10).json()
        if str(res.get("code")) in ["0", "00000", "200"] and res.get("data"):
            data = res["data"]
            if isinstance(data, dict) and data.get("uMarginList"):
                funding_rate = float(data["uMarginList"][-1]) * 100
            elif isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                m_list = latest.get("uMarginList", []) if isinstance(latest, dict) else latest
                if m_list:
                    funding_rate = float(m_list[-1]) * 100
    except Exception as e:
        print(f"⚠️ Lỗi CoinGlass Funding: {e}")

    # 2. LONG/SHORT RATIO
    try:
        url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/longShort_chart?symbol=BTC&time_type=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        if str(res.get("code")) in ["0", "00000", "200"] and res.get("data"):
            data = res["data"]
            if isinstance(data, dict):
                l_list = data.get("longRateList") or data.get("longList") or data.get("longRate") or []
                s_list = data.get("shortRateList") or data.get("shortList") or data.get("shortRate") or []
                if l_list and s_list:
                    long_pct = float(l_list[-1])
                    short_pct = float(s_list[-1])
    except Exception as e:
        print(f"⚠️ Lỗi CoinGlass Long/Short: {e}")

    # 3. TAKER VOLUME
    try:
        url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/vol/chart?symbol=BTC&time_type=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        if str(res.get("code")) in ["0", "00000", "200"] and res.get("data"):
            data = res["data"]
            if isinstance(data, dict):
                b_list = data.get("buyVolList") or data.get("buyVol") or []
                s_list = data.get("sellVolList") or data.get("sellVol") or []
                if b_list and s_list:
                    buy_vol_btc = float(b_list[-1])
                    sell_vol_btc = float(s_list[-1])
    except Exception as e:
        print(f"⚠️ Lỗi CoinGlass Vol: {e}")

    return funding_rate, long_pct, short_pct, buy_vol_btc, sell_vol_btc

def get_fallback_exchanges_data():
    """Lớp dự phòng mới: Lấy dữ liệu từ Binance Futures (Rất ổn định, không cần API Key)."""
    funding_rate, long_pct, short_pct = 0.0, 50.0, 50.0
    buy_vol_btc, sell_vol_btc = 0.0, 0.0

    try:
        # 1. Funding Rate từ Binance Futures
        res_funding = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT",
            timeout=10
        ).json()
        if "lastFundingRate" in res_funding:
            funding_rate = float(res_funding["lastFundingRate"]) * 100

        # 2. Long/Short Ratio từ Binance (Global Account Ratio - 5 phút)
        res_ratio = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1",
            timeout=10
        ).json()
        if isinstance(res_ratio, list) and len(res_ratio) > 0:
            item = res_ratio[0]
            long_pct = float(item.get("longAccount", 0.5)) * 100
            short_pct = float(item.get("shortAccount", 0.5)) * 100

        # 3. Taker Buy/Sell Volume từ Binance (Taker Buy/Sell Ratio - 5 phút)
        res_vol = requests.get(
            "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m&limit=1",
            timeout=10
        ).json()
        if isinstance(res_vol, list) and len(res_vol) > 0:
            item = res_vol[0]
            buy_vol_btc = float(item.get("buyVol", 0.0))
            sell_vol_btc = float(item.get("sellVol", 0.0))

    except Exception as e:
        print(f"Lỗi Fallback Binance: {e}")

    return funding_rate, long_pct, short_pct, buy_vol_btc, sell_vol_btc

def get_market_sentiment_report():
    btc_price = get_btc_price_global()

    # 1. Thử kéo từ CoinGlass trước
    funding_rate, long_pct, short_pct, buy_vol_btc, sell_vol_btc = get_coinglass_data()
    source_name = "COINGLASS VĨ MÔ"

    # 2. Chuyển sang Binance Futures nếu CoinGlass bị chặn / trả về rỗng
    if long_pct == 0.0 or (buy_vol_btc == 0.0 and sell_vol_btc == 0.0):
        print("\n[Thông báo] Chuyển sang nguồn dữ liệu dự phòng (Binance Futures)...")
        funding_rate, long_pct, short_pct, buy_vol_btc, sell_vol_btc = get_fallback_exchanges_data()
        source_name = "BINANCE FUTURES"

    total_vol = buy_vol_btc + sell_vol_btc
    if total_vol > 0:
        buy_vol_pct = (buy_vol_btc / total_vol) * 100.0
        sell_vol_pct = 100.0 - buy_vol_pct
    else:
        buy_vol_pct, sell_vol_pct = 50.0, 50.0

    status = "⚖️ LƯỠNG LỰ (50-50) — Thị trường tích lũy"
    if funding_rate > 0.025 or long_pct > 65:
        status = "🔥 FOMO CAO — Cảnh báo nhịp quét rũ Long!"
    elif funding_rate < -0.01 or short_pct > 65:
        status = "😱 FEAR CAO — Đám đông hoảng loạn, cơ hội rút chân!"

    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    
    report = (
        f"📊 <b>BÁO CÁO DÒNG TIỀN BTC ({source_name})</b>\n"
        f"🕒 <i>Cập nhật: {now_str}</i>\n\n"
        f"🔹 <b>Thị giá BTC:</b> <code>${btc_price:,.2f}</code>\n"
        f"🔹 <b>Funding Rate:</b> <code>{funding_rate:.4f}%</code>\n\n"
        f"👥 <b>TÂM LÝ TÀI KHOẢN (Long/Short Ratio):</b>\n"
        f"   • Phe Long: <code>{long_pct:.1f}%</code> | Phe Short: <code>{short_pct:.1f}%</code>\n\n"
        f"💸 <b>KHỐI LƯỢNG MUA/BÁN CHỦ ĐỘNG (Taker Vol - 5m):</b>\n"
        f"   • Vol Mua (Long): <code>{buy_vol_pct:.1f}%</code> (<code>{buy_vol_btc:,.1f} BTC</code>)\n"
        f"   • Vol Bán (Short): <code>{sell_vol_pct:.1f}%</code> (<code>{sell_vol_btc:,.1f} BTC</code>)\n\n"
        f"📌 <b>Trạng thái:</b> {status}"
    )

    return report

if __name__ == "__main__":
    print("--- BẮT ĐẦU CHẠY BOT ---")
    report_text = get_market_sentiment_report()
    print("\n--- NỘI DUNG BÁO CÁO ---")
    print(report_text)
    send_telegram_message(report_text)
