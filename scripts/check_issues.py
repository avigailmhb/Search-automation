import os
import json
import time
import sys
import requests
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import google.generativeai as genai

# בסיס השאילתה - חסר החלק של created:>=, הוא יתווסף דינמית בזמן ריצה
BASE_QUERY = "org:microsoft is:issue is:open no:assignee label:bug"
SEEN_FILE = "seen_issues.json"

# כמה זמן אחורה לחפש (בשעות). מכסה גם קצת "רזרבה" כדי לא לפספס אם ריצה התעכבה
LOOKBACK_HOURS = 1.5

# כמה Issues חדשים לבדוק לכל היותר בריצה אחת (כדי לא לחרוג ממכסת החינם של Gemini)
MAX_PER_RUN = 10
# כמה שניות להמתין בין קריאה לקריאה ל-Gemini
SECONDS_BETWEEN_CALLS = 4

# תארי כאן בעברית מה מעניין אותך - זה הפרופיל שה-AI בודק מולו
MY_PROFILE = """
מתחילה בפיתוח תוכנה, לומדת פייתון ו-C++.
מחפשת: באגים ברורים עם שחזור פשוט, לא דורשים ידע עמוק בארכיטקטורת ענק,
עדיף בפייתון/C++, לא issue שדורש דיון עיצובי ארוך או ידע דומייני מיוחד.
"""

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


class QuotaExceededError(Exception):
    pass


def build_query():
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{BASE_QUERY} created:>={since_str}"


def search_issues():
    headers = {"Authorization": f"token {os.environ['GH_TOKEN']}"}
    url = "https://api.github.com/search/issues"
    resp = requests.get(
        url,
        headers=headers,
        params={"q": build_query(), "sort": "created", "order": "desc"},
    )
    resp.raise_for_status()
    return resp.json()["items"]


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(ids):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(ids), f)


def ai_filter(issue):
    """שולחת את ה-issue למודל ומקבלת החלטה: מתאים או לא, ולמה."""
    model = genai.GenerativeModel("gemini-2.0-flash")
    prompt = f"""הפרופיל של התורמת:
{MY_PROFILE}

ה-Issue:
כותרת: {issue['title']}
תיאור: {(issue.get('body') or '')[:1500]}

השב אך ורק ב-JSON תקני, בלי טקסט נוסף:
{{"matches": true/false, "reason": "משפט קצר בעברית"}}"""

    for attempt in range(3):
        try:
            resp = model.generate_content(prompt)
            break
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                if attempt == 2:
                    raise QuotaExceededError(str(e))
                time.sleep(20)  # חרגנו ממכסה - נחכה קצת יותר ונסה שוב
            else:
                raise

    text = resp.text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"matches": False, "reason": "שגיאת פענוח"}


def send_email(matched_issues):
    lines = []
    for issue, verdict in matched_issues:
        lines.append(
            f"{issue['title']}\n{issue['html_url']}\nלמה זה מתאים: {verdict['reason']}\n"
        )
    body = "\n---\n".join(lines)
    subject = f"🐛 {len(matched_issues)} באגים חדשים שמתאימים לך"
    send_raw_email(subject, body)


def send_raw_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_FROM"], os.environ["EMAIL_APP_PASSWORD"])
        server.send_message(msg)


def main():
    issues = search_issues()
    seen = load_seen()
    new_issues = [i for i in issues if str(i["id"]) not in seen]

    # בודקים רק MAX_PER_RUN בכל ריצה. השאר יבדקו בריצות הבאות (כל שעה),
    # כי אנחנו לא מוסיפים אותם ל-seen עד שהם נבדקים בפועל.
    to_check = new_issues[:MAX_PER_RUN]

    matched = []
    quota_hit = False

    for i, issue in enumerate(to_check):
        try:
            verdict = ai_filter(issue)
        except QuotaExceededError:
            # נגמרה המכסה של גימיני להיום/לדקה - עוצרים כאן, לא ממשיכים לנסות עוד
            quota_hit = True
            break
        if verdict["matches"]:
            matched.append((issue, verdict))
        seen.add(str(issue["id"]))  # מסמנים כ"נראה" גם אם נדחה, כדי לא לבדוק שוב
        if i < len(to_check) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    if matched:
        send_email(matched)

    if quota_hit:
        send_raw_email(
            "⚠️ בוט מעקב Issues - חריגה ממכסת Gemini",
            "המכסה החינמית של Gemini נגמרה בריצה הזו. "
            "חלק מה-Issues לא נבדקו והם יבדקו שוב בריצה הבאה. "
            "אין צורך לפעול - זה קורה מדי פעם ונפתר לבד תוך שעה.",
        )

    save_seen(seen)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # כל שגיאה בלתי צפויה אחרת - לא מפילים את הריצה בלי הסבר,
        # שולחים מייל עם השגיאה כדי שתדעי מה קרה
        try:
            send_raw_email(
                "⚠️ בוט מעקב Issues - שגיאה בריצה",
                f"הריצה נכשלה עם השגיאה הבאה:\n\n{e}",
            )
        except Exception:
            pass  # אם גם שליחת המייל נכשלת, פשוט לא נשלח כלום
        print(f"Error: {e}")
        sys.exit(0)  # מסיימים בהצלחה כדי שה-Action לא יסומן כ-failed
