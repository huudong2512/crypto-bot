import datetime
import requests

# --- CẤU HÌNH THÔNG TIN CÁ NHÂN CỦA BẠN ---
TELEGRAM_BOT_TOKEN = "8810905518:AAH-l70WmQaQ3v1DqlXu7j9JquX0yJfZpOk"
TELEGRAM_CHAT_ID = "413611713"


def send_telegram_message(text):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  try:
    requests.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )
  except Exception as e:
    print(f"Lỗi gửi Telegram: {e}")


def get_market_data():
  headers = {"User-Agent": "Mozilla/5.0"}

  # 1. Lấy giá BTC từ CoinCap (Không bao giờ bị chặn IP trên GitHub)
  btc_price = 0.0
  try:
    res = requests.get(
        "https://api.coincap.io/v2/assets/bitcoin", headers=headers, timeout=10
    ).json()
    btc_price = float(res["data"]["priceUsd"])
  except Exception as e:
    print(f"Lỗi lấy giá CoinCap: {e}")

  # 2. Lấy Funding Rate từ Bybit API (Cho phép IP GitHub/Mỹ)
  funding_rate = 0.0
  try:
    res = requests.get(
        "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT",
        headers=headers,
        timeout=10,
    ).json()
    data = res["result"]["list"][0]
    funding_rate = float(data["fundingRate"]) * 100
  except Exception as e:
    print(f"Lỗi Funding Bybit: {e}")

  # 3. Lấy Tỷ lệ Long/Short từ Bybit API
  long_pct, short_pct = 50.0, 50.0
  try:
    res = requests.get(
        "https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=5m&limit=1",
        headers=headers,
        timeout=10,
    ).json()
    item = res["result"]["list"][0]
    long_pct = float(item["buyRatio"]) * 100
    short_pct = float(item["sellRatio"]) * 100
  except Exception as e:
    print(f"Lỗi Long/Short Bybit: {e}")

  return btc_price, funding_rate, long_pct, short_pct


def get_market_sentiment_report():
  btc_price, funding_rate, long_pct, short_pct = get_market_data()

  sentiment_status = "⚖️ LƯỠNG LỰ (50-50) — Thị trường tích lũy"
  if funding_rate > 0.025 or long_pct > 65:
    sentiment_status = "🔥 FOMO CAO — Cảnh báo nhịp quét rũ Long!"
  elif funding_rate < -0.01 or short_pct > 65:
    sentiment_status = "😱 FEAR CAO — Đám đông hoảng loạn, cơ hội rút chân!"

  now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M UTC")
  report = (
      f"📊 <b>BÁO CÁO TÂM LÝ THỊ TRƯỜNG BTC</b>\n"
      f"🕒 <i>Cập nhật: {now_str}</i>\n\n"
      f"🔹 <b>Thị giá BTC:</b> <code>${btc_price:,.2f}</code>\n"
      f"🔹 <b>Funding Rate:</b> <code>{funding_rate:.4f}%</code>\n\n"
      f"👥 <b>TÂM LÝ TÀI KHOẢN (Bybit Ratio):</b>\n"
      f"   • Phe Long: <code>{long_pct:.1f}%</code> | Phe Short:"
      f" <code>{short_pct:.1f}%</code>\n\n"
      f"📌 <b>Trạng thái:</b> {sentiment_status}"
  )

  return report


if __name__ == "__main__":
  report_text = get_market_sentiment_report()
  print(report_text)
  send_telegram_message(report_text)
