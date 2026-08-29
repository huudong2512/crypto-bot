import datetime
import requests

# --- CẤU HÌNH THÔNG TIN CỦA BẠN ---
TELEGRAM_BOT_TOKEN = "8810905518:AAH-l70WmQaQ3v1DqlXu7j9JquX0yJfZpOk"
TELEGRAM_CHAT_ID = "413611713"
COINGLASS_API_KEY = "f8669ec5c6824c2286dcaf5217fb3204"


def send_telegram_message(text):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f"Lỗi gửi Telegram: {e}")


def get_btc_price_global():
  """Lấy giá BTC từ CoinGecko (Không bao giờ bị chặn Cloud/US IP)."""
  try:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    res = requests.get(url, timeout=10).json()
    return float(res["bitcoin"]["usd"])
  except Exception:
    return 0.0


def get_coinglass_data():
  headers = {
      "accept": "application/json",
      "CG-API-KEY": COINGLASS_API_KEY,
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      ),
  }

  btc_price = get_btc_price_global()
  funding_rate = 0.0
  long_acc_pct, short_acc_pct = 50.0, 50.0
  buy_vol_btc, sell_vol_btc = 0.0, 0.0
  buy_vol_pct, sell_vol_pct = 50.0, 50.0

  # 1. KÉO FUNDING RATE TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/funding_rates/chart?symbol=BTC&type=0"
    res = requests.get(url, headers=headers, timeout=10).json()
    if res.get("code") == "0" and res.get("data"):
      # Lấy chỉ số Funding mới nhất
      data_list = res["data"]
      if data_list:
        latest = data_list[-1]
        funding_rate = float(latest.get("uMarginList", [0])[0]) * 100
  except Exception as e:
    print(f"CoinGlass Funding Error: {e}")

  # 2. KÉO TỶ LỆ LONG/SHORT CỦA TOÀN THỊ TRƯỜNG TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/longShort_chart?symbol=BTC&time_type=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    if res.get("code") == "0" and res.get("data"):
      data = res["data"]
      long_list = data.get("longList", [])
      short_list = data.get("shortList", [])
      if long_list and short_list:
        long_acc_pct = float(long_list[-1])
        short_acc_pct = float(short_list[-1])
  except Exception as e:
    print(f"CoinGlass Long/Short Error: {e}")

  # 3. KÉO KHỐI LƯỢNG TAKER BUY/SELL VOL TỪ COINGLASS
  try:
    url = "https://open-api-v3.coinglass.com/api/pro/v1/futures/vol/chart?symbol=BTC&time_type=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    if res.get("code") == "0" and res.get("data"):
      data = res["data"]
      buy_list = data.get("buyVolList", [])
      sell_list = data.get("sellVolList", [])
      if buy_list and sell_list:
        buy_vol_btc = float(buy_list[-1])
        sell_vol_btc = float(sell_list[-1])
        total = buy_vol_btc + sell_vol_btc
        if total > 0:
          buy_vol_pct = (buy_vol_btc / total) * 100
          sell_vol_pct = 100 - buy_vol_pct
  except Exception as e:
    print(f"CoinGlass Vol Error: {e}")

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
  report_text = get_market_sentiment_report()
  send_telegram_message(report_text)
  print("Đã gửi báo cáo CoinGlass về Telegram thành công!")
