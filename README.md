# Issue Watcher

בוט שבודק כל שעה אם יש Issues חדשים מסוג "bug" ב-org:microsoft שתואמים לפרופיל שלך, ושולח עליהם מייל.

## התקנה

1. חלצי את הקבצים והעלי אותם לריפו בגיטהאב (שומרים על אותו מבנה תיקיות בדיוק):
   - `.github/workflows/watch-issues.yml`
   - `scripts/check_issues.py`
   - `seen_issues.json`

2. הוסיפי ב-Settings → Secrets and variables → Actions את חמשת ה-Secrets:
   - `GH_TOKEN` — Fine-grained Personal Access Token מגיטהאב (הרשאת קריאה בלבד)
   - `GEMINI_API_KEY` — מפתח מ-aistudio.google.com
   - `EMAIL_FROM` — כתובת הג'ימייל השולחת
   - `EMAIL_TO` — כתובת המייל שאליה יישלחו ההתראות
   - `EMAIL_APP_PASSWORD` — App Password של הג'ימייל (16 תווים)

3. בטאב Actions, הריצי את ה-workflow `watch-issues` ידנית (Run workflow) לבדיקה ראשונה.

## התאמה אישית

- כדי לשנות את שאילתת החיפוש — ערכי את המשתנה `QUERY` בקובץ `scripts/check_issues.py`.
- כדי לשנות את הקריטריונים לסינון החכם — ערכי את המשתנה `MY_PROFILE` באותו קובץ.
- כדי לשנות את התדירות — ערכי את שורת ה-`cron` בקובץ ה-workflow (כרגע: כל שעה).
