import datetime
import requests

# --- CẤU HÌNH THÔNG TIN CÁ NHÂN CỦA BẠN ---
TELEGRAM_BOT_TOKEN = "8810905518:AAH-l70WmQaQ3v1DqlXu7j9JquX0yJfZpOk"
TELEGRAM_CHAT_ID = "413611713"


def send_telegram_message(text):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f"Lỗi gửi Telegram: {e}")


def get_market_data():
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      )
  }

  btc_price = 0.0
  funding_rate = 0.0
  long_acc_pct, short_acc_pct = 50.0, 50.0
  buy_vol_btc, sell_vol_btc = 0.0, 0.0
  buy_vol_pct, sell_vol_pct = 50.0, 50.0

  # 1. Giá BTC & Funding Rate từ Binance Futures
  try:
    url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
    res = requests.get(url, headers=headers, timeout=10).json()
    funding_rate = float(res.get("lastFundingRate", 0)) * 100
  except Exception as e:
    print(f"Funding error: {e}")

  try:
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    res = requests.get(url, headers=headers, timeout=10).json()
    btc_price = float(res.get("price", 0))
  except Exception as e:
    print(f"Price error: {e}")

  # 2. Tỷ lệ Tài khoản Long/Short từ Binance Futures
  try:
    url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    if isinstance(res, list) and len(res) > 0:
      long_acc_pct = float(res[0].get("longAccount", 0.5)) * 100
      short_acc_pct = float(res[0].get("shortAccount", 0.5)) * 100
  except Exception as e:
    print(f"Account ratio error: {e}")

  # 3. Khối lượng Mua/Bán Chủ động (Taker Vol) từ Binance Futures
  try:
    url = "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m&limit=1"
    res = requests.get(url, headers=headers, timeout=10).json()
    if isinstance(res, list) and len(res) > 0:
      buy_vol_btc = float(res[0].get("buyVol", 0))
      sell_vol_btc = float(res[0].get("sellVol", 0))
      total = buy_vol_btc + sell_vol_btc
      if total > 0:
        buy_vol_pct = (buy_vol_btc / total) * 100
        sell_vol_pct = 100 - buy_vol_pct
  except Exception as e:
    print(f"Taker vol error: {e}")

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
  ) = get_market_data()

  sentiment_status = "⚖️ LƯỠNG LỰ (50-50) — Thị trường tích lũy"
  if funding_rate > 0.025 or long_acc_pct > 65:
    sentiment_status = "🔥 FOMO CAO — Cảnh báo nhịp quét rũ Long!"
  elif funding_rate < -0.01 or short_acc_pct > 65:
    sentiment_status = "😱 FEAR CAO — Đám đông hoảng loạn, cơ hội rút chân!"

  now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M UTC")
  report = (
      f"📊 <b>BÁO CÁO TÂM LÝ & DÒNG TIỀN BTC</b>\n"
      f"🕒 <i>Cập nhật: {now_str}</i>\n\n"
      f"🔹 <b>Thị giá BTC:</b> <code>${btc_price:,.2f}</code>\n"
      f"🔹 <b>Funding Rate:</b> <code>{funding_rate:.4f}%</code>\n\n"
      f"👥 <b>TÂM LÝ TÀI KHOẢN (Account Ratio):</b>\n"
      f"   • Phe Long: <code>{long_acc_pct:.1f}%</code> | Phe Short:"
      f" <code>{short_acc_pct:.1f}%</code>\n\n"
      f"💸 <b>DÒNG TIỀN KHỐI LƯỢNG (Taker Vol 5M):</b>\n"
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