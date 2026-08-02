import os
import json
import time
import requests
import smtplib
from email.mime.text import MIMEText
import google.generativeai as genai

# שינוי כאן משנה מה מחפשים. label:bug מגביל רק לבאגים.
QUERY = "org:microsoft is:issue is:open no:assignee label:bug created:>=2026-08-01"
SEEN_FILE = "seen_issues.json"

# כמה Issues חדשים לבדוק לכל היותר בריצה אחת (כדי לא לחרוג ממכסת החינם של Gemini)
MAX_PER_RUN = 10
# כמה שניות להמתין בין קריאה לקריאה ל-Gemini
SECONDS_BETWEEN_CALLS = 4

# תארי כאן בעברית מה מעניין אותך - זה הפרופיל שה-AI בודק מולו
MY_PROFILE = """
מתחילה בפיתוח תוכנה, עם ניסיון בסיסי־בינוני ב־Python, Git, GitHub, בדיקות וקריאת קוד קיים. יש לה היכרות עם C++ ומוכנה לעבוד גם ב־C# או TypeScript כאשר היקף המשימה ברור.

מחפשת Issues בקוד פתוח, בעיקר בפרויקטים של Microsoft, שעומדים ברוב התנאים הבאים:

באג ברור עם תיאור טכני ממוקד.
צעדי שחזור פשוטים ומלאים.
התנהגות צפויה מול התנהגות בפועל.
שינוי קטן עד בינוני, רצוי בטווח של כמה שעות עד יום עבודה.
אזור קוד ממוקד, ללא צורך להבין ארכיטקטורה של מערכת שלמה.
אפשרות לכתוב או לעדכן טסטים שמוכיחים את התיקון.
עדיפות ל־Python, C++ או קוד תשתיתי פשוט.
ללא צורך בידע דומייני מיוחד, חשבון ענן, שירות חיצוני או הרשאות פנימיות.
ללא דיון עיצובי, UX או החלטת מוצר שטרם הוכרעה.
ללא שינויי API ציבוריים מורכבים, migrations או תאימות לאחור רחבה.
ללא Assignee, PR מקושר או branch שכבר מכיל מימוש.
לא Issue אוטומטי, tracking issue, release task, roadmap item או דוח תחזוקה.
עדיפות ל־Issues חדשים, מסומנים bug, help wanted או good first issue.
עדיפות לתיקוני validation, parsing, error handling, CLI, configuration, compatibility, tests, logging או package metadata.
"""

genai.configure(api_key=os.environ["GEMINI_API_KEY"])


def search_issues():
    headers = {"Authorization": f"token {os.environ['GH_TOKEN']}"}
    url = "https://api.github.com/search/issues"
    resp = requests.get(
        url,
        headers=headers,
        params={"q": QUERY, "sort": "created", "order": "desc"},
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
                time.sleep(20)  # חרגנו ממכסה - נחכה קצת יותר ונסה שוב
            else:
                raise
    else:
        return {"matches": False, "reason": "נכשל אחרי כמה ניסיונות"}

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

    msg = MIMEText(body)
    msg["Subject"] = f"🐛 {len(matched_issues)} באגים חדשים שמתאימים לך"
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
    for i, issue in enumerate(to_check):
        verdict = ai_filter(issue)
        if verdict["matches"]:
            matched.append((issue, verdict))
        seen.add(str(issue["id"]))  # מסמנים כ"נראה" גם אם נדחה, כדי לא לבדוק שוב
        if i < len(to_check) - 1:
            time.sleep(SECONDS_BETWEEN_CALLS)

    if matched:
        send_email(matched)

    save_seen(seen)


if __name__ == "__main__":
    main()
