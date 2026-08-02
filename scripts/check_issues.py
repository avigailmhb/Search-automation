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

# כמה זמן אחורה לחפש (בשעות). מכסה "רזרבה" רחבה כדי לא לפספס אם ריצה קודמת דולגה
LOOKBACK_HOURS = 3

# כמה Issues חדשים לבדוק לכל היותר בריצה אחת (כדי לא לחרוג ממכסת החינם של Gemini)
MAX_PER_RUN = 5
# כמה שניות להמתין בין קריאה לקריאה ל-Gemini
SECONDS_BETWEEN_CALLS = 15

# תארי כאן בעברית מה מעניין אותך - זה הפרופיל שה-AI בודק מולו
MY_PROFILE = """
מתחילה בפיתוח תוכנה, עם ניסיון בסיסי־בינוני ב-Python, Git, GitHub, בדיקות וקריאת קוד קיים.
יש לה היכרות עם C++ ומוכנה לעבוד גם ב-C# או TypeScript כאשר היקף המשימה ברור.

מחפשת Issues בקוד פתוח, בעיקר בפרויקטים של Microsoft, שעומדים ברוב התנאים הבאים:
- באג ברור עם תיאור טכני ממוקד.
- צעדי שחזור פשוטים ומלאים.
- התנהגות צפויה מול התנהגות בפועל.
- שינוי קטן עד בינוני, רצוי בטווח של כמה שעות עד יום עבודה.
- אזור קוד ממוקד, ללא צורך להבין ארכיטקטורה של מערכת שלמה.
- אפשרות לכתוב או לעדכן טסטים שמוכיחים את התיקון.
- עדיפות ל-Python, C++ או קוד תשתיתי פשוט.
- ללא צורך בידע דומייני מיוחד, חשבון ענן, שירות חיצוני או הרשאות פנימיות.
- ללא דיון עיצובי, UX או החלטת מוצר שטרם הוכרעה.
- ללא שינויי API ציבוריים מורכבים, migrations או תאימות לאחור רחבה.
- ללא Assignee, PR מקושר או branch שכבר מכיל מימוש.
- לא Issue אוטומטי, tracking issue, release task, roadmap item או דוח תחזוקה.
- עדיפות ל-Issues חדשים, מסומנים bug, help wanted או good first issue.
- עדיפות לתיקוני validation, parsing, error handling, CLI, configuration, compatibility, tests, logging או package metadata.
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


def send_summary_email(matched, rejected):
    lines = []

    if matched:
        lines.append(f"✔ נמצאו {len(matched)} התאמות:\n")
        for issue, verdict in matched:
            lines.append(
                f"{issue['title']}\n{issue['html_url']}\nלמה זה מתאים: {verdict['reason']}\n"
            )

    if rejected:
        lines.append(f"\n✘ נבדקו ונדחו ({len(rejected)}):\n")
        for issue, verdict in rejected:
            lines.append(
                f"{issue['title']}\n{issue['html_url']}\nלמה נדחה: {verdict['reason']}\n"
            )

    body = "\n---\n".join(lines) if lines else "לא נמצאו Issues חדשים לבדיקה בריצה הזו."

    if matched:
        subject = f"🐛 {len(matched)} התאמות, {len(rejected)} נדחו"
    else:
        subject = f"ℹ️ נבדקו {len(rejected)} Issues - אין התאמות הפעם"

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
    query = build_query()
    print(f"שאילתה: {query}")

    issues = search_issues()
    print(f"נמצאו {len(issues)} Issues שעונים על השאילתה הבסיסית")

    seen = load_seen()
    new_issues = [i for i in issues if str(i["id"]) not in seen]
    print(f"מתוכם {len(new_issues)} חדשים (לא נבדקו בעבר)")

    to_check = new_issues[:MAX_PER_RUN]
    print(f"בודקים {len(to_check)} מתוכם בריצה הזו (מקסימום {MAX_PER_RUN})")

    matched = []
    rejected = []
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
            print(f"✔ מתאים: {issue['title']} - {verdict['reason']}")
        else:
            rejected.append((issue, verdict))
            print(f"✘ לא מתאים: {issue['title']} - {verdict['reason']}")
        seen.add(str(issue["id"]))  # מסמנים כ"נראה" גם אם נדחה, כדי לא לבדוק שוב
        if i < len(to_check) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    if matched or rejected:
        send_summary_email(matched, rejected)
        print(f"נשלח מייל: {len(matched)} התאמות, {len(rejected)} נדחו")
    else:
        print("לא נמצאו Issues חדשים לבדיקה - לא נשלח מייל")

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
