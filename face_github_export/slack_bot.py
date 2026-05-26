import os
import re
import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import database

# Ensure you set these environment variables before running!
# SLACK_BOT_TOKEN="xoxb-..."
# SLACK_APP_TOKEN="xapp-..."

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

@app.message(re.compile(r"(.*)[:：]\s*(.*)で(\d+)分遅延", re.IGNORECASE))
def handle_delay_message(message, say, context):
    """
    Parses messages like: "Mendsanaa: 中央線で30分遅延します"
    Regex captures:
      1: Mendsanaa (student name)
      2: 中央線 (commute line)
      3: 30 (delay minutes)
    """
    student_name = context['matches'][0].strip()
    commute_line = context['matches'][1].strip()
    try:
        delay_minutes = int(context['matches'][2])
    except ValueError:
        delay_minutes = 0

    if delay_minutes > 0:
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # Save to database
        database.log_slack_delay_request(student_name, commute_line, delay_minutes, today_str)
        
        # Reply to the user
        say(f"✅ 了解しました！{student_name}さんの{commute_line}の遅延（{delay_minutes}分）をシステムに登録しました。\n"
            f"本日は遅刻判定の時間を{delay_minutes}分延長して待機しています。気をつけてお越しください！")
    else:
        say("分数の読み取りに失敗しました。例: 『Mendsanaa: 中央線で30分遅延します』のように送信してください。")

@app.message("テスト")
def handle_test_message(message, say):
    say("Slack Botは正常に稼働しています！")

@app.event("message")
def handle_message_events(body, say, logger):
    text = body.get('event', {}).get('text', '')
    logger.info(f"Received message: {body}")
    with open("slack_debug.txt", "a", encoding="utf-8") as f:
        f.write(f"Received text: {text}\n")
    
    # Check if the bot hasn't replied yet
    if "遅延" in text and ":" not in text and "：" not in text:
        say("「名前: 路線で〇〇分遅延」のフォーマットで送信してください！例：『Mende: 中央線で30分遅延します』")
    elif "遅延" in text:
        say("文章の形式が少し違うようです。例：『Mende: 中央線で30分遅延します』とそのままコピペして試してみてください！")

if __name__ == "__main__":
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    
    if not bot_token or not app_token:
        print("エラー: SLACK_BOT_TOKEN と SLACK_APP_TOKEN が設定されていません。")
        print("実行前に環境変数を設定してください。")
        exit(1)
        
    print("Slack Bot is running! Waiting in Socket Mode...")
    handler = SocketModeHandler(app, app_token)
    handler.start()
