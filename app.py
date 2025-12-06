# api/index.py
from flask import Flask, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent,
    FollowEvent,
    TextMessageContent,
)
from openai import OpenAI
import os

app = Flask(__name__)

# 從環境變數讀取 LINE & OpenAI 設定
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
line_handler = WebhookHandler(CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# 簡單的 in-memory 對話歷史（key = user_id, value = 整段文字）
# 注意：部署在 Vercel 時，instance 重啟或換機器時，歷史會被清掉
conversations = {}

SYSTEM_PROMPT = "你是一個友善的助理，請用繁體中文簡短回答。"


def chat_with_llm(user_id: str, user_text: str) -> str:
    """
    依照 user_id 維護對話歷史，呼叫 LLM 並回傳回答文字
    """
    # 取得這個 user 之前的對話歷史，沒有就建立新的
    history = conversations.get(user_id)
    if history is None:
        history = SYSTEM_PROMPT + "\n\n"

    # 把新的輸入加進去
    history += f"使用者：{user_text}\n"

    # 呼叫 LLM，讓它接續當「助理」回答
    response = client.responses.create(
        model="gpt-4.1-mini",
        input=history + "助理：",
    )

    assistant_reply = response.output[0].content[0].text.strip()

    # 把助理回答也加進歷史
    history += f"助理：{assistant_reply}\n"
    conversations[user_id] = history

    return assistant_reply


@app.route("/", methods=["GET"])
def index():
    return "Line bot with LLM is running", 200


@app.route("/callback", methods=["POST"])
def callback():
    """
    給 LINE Webhook 用的 endpoint
    Vercel 上路徑會是 /api/callback
    """
    try:
        signature = request.headers.get("X-Line-Signature", "")
        body = request.get_data(as_text=True)
        app.logger.info("Request body: " + body)

        line_handler.handle(body, signature)

    except InvalidSignatureError:
        app.logger.error("Invalid signature. 請確認 CHANNEL_SECRET 是否正確。")
    except Exception as e:
        app.logger.exception(f"Webhook error: {e}")

    # 無論如何都回 200，避免 LINE 說 500
    return "OK", 200


@line_handler.add(FollowEvent)
def handle_follow(event):
    app.logger.info(f"Got {event.type} event (user followed bot)")


@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """
    收到文字訊息時的處理：
    - 'reset' / '重置對話'：清除這個 user 的對話歷史
    - 其他：丟給 LLM，維持對話上下文
    """
    try:
        user_id = event.source.user_id
        user_text = event.message.text.strip()

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)

            # 指令：reset 對話
            if user_text.lower() in ("reset", "重置對話"):
                conversations.pop(user_id, None)
                reply_text = "已重置對話記錄，我們重新開始聊天～"
            else:
                # 一般情況：走 LLM 對話
                reply_text = chat_with_llm(user_id, user_text)

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

    except Exception as e:
        app.logger.exception(f"Handle message error: {e}")


# ⚠ Vercel 不會跑這段，但你在本機要測試可以用
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
