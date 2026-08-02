import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
import google.generativeai as genai

# שינוי כאן משנה מה מחפשים. label:bug מגביל רק לבאגים.
QUERY = "org:microsoft is:issue is:open no:assignee label:bug created:>=2026-08-01"
SEEN_FILE = "seen_issues.json"

# תארי כאן בעברית מה מעניין אותך - זה הפרופיל שה-AI בודק מולו
MY_PROFILE = """
מתחילה בפיתוח תוכנה, לומדת פייתון ו-C++.
מחפשת: באגים ברורים עם שחזור פשוט, לא דורשים ידע עמוק בארכיטקטורת ענק,
עדיף בפייתון/C++, לא issue שדורש דיון עיצובי ארוך או ידע דומייני מיוחד.
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

    resp = model.generate_content(prompt)
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

    matched = []
    for issue in new_issues:
        verdict = ai_filter(issue)
        if verdict["matches"]:
            matched.append((issue, verdict))
        seen.add(str(issue["id"]))  # מסמנים כ"נראה" גם אם נדחה, כדי לא לבדוק שוב

    if matched:
        send_email(matched)

    save_seen(seen)


if __name__ == "__main__":
    main()
