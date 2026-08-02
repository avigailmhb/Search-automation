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
