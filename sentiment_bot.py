import datetime
import requests

# --- CẤU HÌNH THÔNG TIN CÁ NHÂN CỦA BẠN ---
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


def get_btc_price():
  """Lấy thị giá BTC từ Coinbase hoặc Binance Spot (100% không bị chặn trên Cloud/GitHub)."""
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      )
  }

  # 1. Thử Coinbase API (Không giới hạn IP)
  try:
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    res = requests.get(url, headers=headers, timeout=10).json()
    price = float(res["data"]["amount"])
    if price > 0:
      print(f"✓ Lấy giá BTC Coinbase thành công: ${price:,.2f}")
      return price
  except Exception as e:
    print(f"Lỗi Coinbase price: {e}")

  # 2. Dự phòng Binance Spot API (Gốc api.binance.com không bị geoblock)
  try:
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    res = requests.get(url, headers=headers, timeout=10).json()
    price = float(res.get("price", 0))
    if price > 0:
      print(f"✓ Lấy giá BTC Binance Spot thành công: ${price:,.2f}")
      return price
  except Exception as e:
    print(f"Lỗi Binance Spot price: {e}")

  return 0.0


def get_coinglass_data():
  headers = {
      "accept": "application/json",
      "CG-API-KEY": COINGLASS_API_KEY,
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      ),
  }

  btc_price = get_btc_price()
  funding_rate = 0.0
  long_acc_pct, short_acc_pct = 50.0, 50.0
  buy_vol_btc, sell_vol_btc = 0.0, 0.0
  buy_vol_pct, sell_vol_pct = 50.0, 50.0

  # 1. KÉO FUNDING RATE TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/funding_rates/chart?symbol=BTC&type=0"
    res = requests.get(url, headers=headers, timeout=10).json()
    code_str = str(res.get("code", ""))
    if code_str in ["0", "00000", "200"] and res.get("data"):
      data = res["data"]
      if isinstance(data, list) and len(data) > 0:
        latest = data[-1]
        if isinstance(latest, dict):
          m_list = latest.get("uMarginList", [])
          if isinstance(m_list, list) and len(m_list) > 0:
            funding_rate = float(m_list[-1]) * 100
      elif isinstance(data, dict):
        m_list = data.get("uMarginList", [])
        if isinstance(m_list, list) and len(m_list) > 0:
          funding_rate = float(m_list[-1]) * 100
      print(f"✓ CoinGlass Funding Rate: {funding_rate:.4f}%")
    else:
      print(f"⚠️ CoinGlass Funding Response: {res}")
  except Exception as e:
    print(f"❌ Lỗi CoinGlass Funding: {e}")

  # 2. KÉO TỶ LỆ LONG/SHORT TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/longShort_chart?symbol=BTC&time_type=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    code_str = str(res.get("code", ""))
    if code_str in ["0", "00000", "200"] and res.get("data"):
      data = res["data"]
      if isinstance(data, dict):
        l_list = data.get("longList", [])
        s_list = data.get("shortList", [])
        if (
            isinstance(l_list, list)
            and len(l_list) > 0
            and isinstance(s_list, list)
            and len(s_list) > 0
        ):
          long_acc_pct = float(l_list[-1])
          short_acc_pct = float(s_list[-1])
      elif isinstance(data, list) and len(data) > 0:
        latest = data[-1]
        if isinstance(latest, dict):
          l_list = latest.get("longList", [])
          s_list = latest.get("shortList", [])
          if (
              isinstance(l_list, list)
              and len(l_list) > 0
              and isinstance(s_list, list)
              and len(s_list) > 0
          ):
            long_acc_pct = float(l_list[-1])
            short_acc_pct = float(s_list[-1])
      print(
          f"✓ CoinGlass Long/Short Ratio: Long {long_acc_pct:.1f}% | Short"
          f" {short_acc_pct:.1f}%"
      )
    else:
      print(f"⚠️ CoinGlass Long/Short Response: {res}")
  except Exception as e:
    print(f"❌ Lỗi CoinGlass Long/Short: {e}")

  # 3. KÉO KHỐI LƯỢNG TAKER BUY/SELL VOL TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/vol/chart?symbol=BTC&time_type=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    code_str = str(res.get("code", ""))
    if code_str in ["0", "00000", "200"] and res.get("data"):
      data = res["data"]
      if isinstance(data, dict):
        b_list = data.get("buyVolList", [])
        s_list = data.get("sellVolList", [])
        if (
            isinstance(b_list, list)
            and len(b_list) > 0
            and isinstance(s_list, list)
            and len(s_list) > 0
        ):
          buy_vol_btc = float(b_list[-1])
          sell_vol_btc = float(s_list[-1])
          tot = buy_vol_btc + sell_vol_btc
          if tot > 0:
            buy_vol_pct = (buy_vol_btc / tot) * 100
            sell_vol_pct = 100 - buy_vol_pct
      elif isinstance(data, list) and len(data) > 0:
        latest = data[-1]
        if isinstance(latest, dict):
          b_list = latest.get("buyVolList", [])
          s_list = latest.get("sellVolList", [])
          if (
              isinstance(b_list, list)
              and len(b_list) > 0
              and isinstance(s_list, list)
              and len(s_list) > 0
          ):
            buy_vol_btc = float(b_list[-1])
            sell_vol_btc = float(s_list[-1])
            tot = buy_vol_btc + sell_vol_btc
            if tot > 0:
              buy_vol_pct = (buy_vol_btc / tot) * 100
              sell_vol_pct = 100 - buy_vol_pct
      print(
          f"✓ CoinGlass Taker Vol: Buy {buy_vol_btc:,.1f} BTC,"
          f" Sell {sell_vol_btc:,.1f} BTC"
      )
    else:
      print(f"⚠️ CoinGlass Taker Vol Response: {res}")
  except Exception as e:
    print(f"❌ Lỗi CoinGlass Taker Vol: {e}")

  return (
      btc_price,
      funding_rate,
      long_acc_pct,
      short_acc_pct,
      buy_vol_btc,
      sell_vol_btc,
      buy_vol_pct,
      sell_vol_pct,
  )


def get_market_sentiment_report():
  (
      btc_price,
      funding_rate,
      long_acc_pct,
      short_acc_pct,
      buy_vol_btc,
      sell_vol_btc,
      buy_vol_pct,
      sell_vol_pct,
  ) = get_coinglass_data()

  sentiment_status = "⚖️ LƯỠNG LỰ (50-50) — Thị trường tích lũy"
  if funding_rate > 0.025 or long_acc_pct > 65:
    sentiment_status = "🔥 FOMO CAO — Cảnh báo nhịp quét rũ Long!"
  elif funding_rate < -0.01 or short_acc_pct > 65:
    sentiment_status = "😱 FEAR CAO — Đám đông hoảng loạn, cơ hội rút chân!"

  now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M UTC")
  report = (
      f"📊 <b>BÁO CÁO TÂM LÝ & DÒNG TIỀN COINGLASS</b>\n"
      f"🕒 <i>Cập nhật: {now_str}</i>\n\n"
      f"🔹 <b>Thị giá BTC:</b> <code>${btc_price:,.2f}</code>\n"
      f"🔹 <b>Funding Rate:</b> <code>{funding_rate:.4f}%</code>\n\n"
      f"👥 <b>TÂM LÝ TÀI KHOẢN (Toàn thị trường):</b>\n"
      f"   • Phe Long: <code>{long_acc_pct:.1f}%</code> | Phe Short:"
      f" <code>{short_acc_pct:.1f}%</code>\n\n"
      f"💸 <b>DÒNG TIỀN KHỐI LƯỢNG (Taker Vol):</b>\n"
      f"   • Vol Mua (Long): <code>{buy_vol_pct:.1f}%</code>"
      f" (<code>{buy_vol_btc:,.1f} BTC</code>)\n"
      f"   • Vol Bán (Short): <code>{sell_vol_pct:.1f}%</code>"
      f" (<code>{sell_vol_btc:,.1f} BTC</code>)\n\n"
      f"📌 <b>Trạng thái:</b> {sentiment_status}"
  )

  return report


if __name__ == "__main__":
  print("--- BẮT ĐẦU CHẠY SCRIPT ---")
  report_text = get_market_sentiment_report()
  print("\n--- NỘI DUNG BÁO CÁO ---")
  print(report_text)
  send_telegram_message(report_text)
