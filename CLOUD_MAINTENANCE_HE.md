<div dir="rtl">

# תחזוקת הפרויקט בענן

## מה מחובר למה

- קוד הייצור נמצא ב־GitHub: `noanoa2162-ops/open_source_project`, בענף `main`.
- Vercel בונה אוטומטית כל commit חדש שמגיע ל־`main` ומעדכן את `https://tofes-demo-noa-2162.vercel.app`.
- Vercel מפעיל שני containers: שרת האתר מתוך `Dockerfile.vercel`, ועובד רקע מתוך `Dockerfile.worker`.
- Supabase שומר את נתוני האפליקציה ב־PostgreSQL בסכמה הפרטית `app_private` ואת הקבצים ב־Storage bucket הפרטי `documents`.
- מפתח Anthropic שמור רק כ־Environment Variable סודי ב־Vercel.
- בהאקתון `PUBLIC_DEMO=1`: אין מסך כניסה. בכניסה הראשונה השרת מנפיק לדפדפן
  עוגיית Session חתומה, זמנית, `Secure` ו־`HttpOnly`. `ACCESS_PASSWORD` משמש רק
  כסוד חתימה בצד השרת, ומפתח Anthropic אינו נשלח לקוד ה־React.

## פרסום שינוי קוד

1. עובדים על ענף חדש במאגר `noanoa2162-ops/open_source_project`.
2. מעדכנים בדיקות ואת קובצי התלויות הנדרשים.
3. עושים Push ופותחים Pull Request אל `main`.
4. בודקים ש־GitHub Actions עבר וש־Vercel Preview נבנה.
5. ממזגים ל־`main`. המיזוג מפעיל Production deployment אוטומטי ב־Vercel.
6. בודקים ב־Vercel שה־deployment במצב Ready, שה־daemon בשם `document-worker` במצב Alive, ואז מבצעים בדיקת שימוש קצרה באתר.

Push למאגר `yk6749841-ai/open_source_project` אינו מעדכן את האתר. כדי שהחברה תוכל לפרסם, מוסיפים אותה כ־collaborator למאגר שלך והיא עובדת מולו. מומלץ להשתמש ב־Pull Request ולא לדחוף שינוי גדול ישירות ל־`main`.

## תלויות חדשות ו־Docker

- חבילת Python חדשה: מעדכנים גם `pyproject.toml` וגם `uv.lock`. הפקודה המקובלת היא `uv add NAME`.
- חבילת מערכת כמו Tesseract, LibreOffice או ספריית Linux: מעדכנים את פקודת `apt-get install` גם ב־`Dockerfile.vercel` וגם ב־`Dockerfile.worker` אם שני השירותים משתמשים בה.
- שינוי נתיב, שם מודול או פקודת הפעלה: בודקים את שני Dockerfiles ואת `vercel.json`.
- אין לשמור מידע קבוע בדיסק של container. מידע קבוע חייב להישמר ב־PostgreSQL או ב־Supabase Storage.
- אין להכניס `.env`, מפתחות API, סיסמאות או מסמכים אמיתיים ל־GitHub או ל־Docker image.

## החלפת מפתח Anthropic

1. יוצרים מפתח חדש בחשבון Anthropic.
2. ב־Vercel פותחים את הפרויקט `tofes-demo-noa-2162` ואז Environment Variables.
3. עורכים את `ANTHROPIC_API_KEY`, שומרים אותו כ־Secret עבור Production ולוחצים Redeploy.
4. מעדכנים גם את `ANTHROPIC_API_KEY` בקובץ המקומי `.env.cloud.local` כדי שיהיה עותק התחזוקה הנכון.
5. אחרי שהפריסה החדשה תקינה, מבטלים את המפתח הישן ב־Anthropic.

שינוי Environment Variable משפיע רק על deployment חדש. שמירה ללא Redeploy אינה מעדכנת את התהליך שכבר רץ.

## החלפת פרטי הכניסה לאתר

1. לדמו הציבורי משאירים ב־Vercel את `PUBLIC_DEMO=1`; אין שם משתמש או סיסמה במסך.
2. `ACCESS_PASSWORD` עדיין חייב להיות סוד אקראי באורך 24 תווים לפחות. הוא חותם
   Sessions בצד השרת ואינו מוצג למשתמשת.
3. בשימוש עתידי של המעבדה עם מידע אמיתי משנים את `PUBLIC_DEMO` ל־`0`, מבצעים
   Redeploy, ואז מסך הכניסה משתמש ב־`ACCESS_USER` וב־`ACCESS_PASSWORD`.
4. שינוי סוד החתימה מבטל אוטומטית את כל Sessions הישנים.

## גישה למסד ולמסמכים

- Dashboard: `https://supabase.com/dashboard/project/yndbhnlqvyfstceyormr`
- נתוני האפליקציה: SQL Editor, בסכמה `app_private`.
- מסמכים: Storage → bucket בשם `documents`.
- מצב ומשאבים: Project Overview ו־Organization Usage.

למחיקת טופס או אצווה משתמשים קודם בכפתורי המחיקה באתר. כך האפליקציה יכולה למחוק בהתאמה את הרשומות ואת הקבצים.

לא מוחקים ידנית שורות מטבלאות `storage.*`: הן מטא־דאטה בלבד, ומחיקה כזאת עלולה להשאיר קבצים מיותמים. גם מחיקת שורות מ־`app_private` בלי מחיקת הקבצים המתאימים עלולה ליצור חוסר התאמה.

מחיקת כל פרויקט Supabase דרך Project Settings היא פעולה סופית: היא מוחקת את כל ה־DB, הקבצים והגדרות הפרויקט. לפני פעולה כזאת מייצאים גיבוי של ה־DB ושל Storage, מעדכנים את Vercel לפרויקט חלופי, ורק אז מוחקים.

## החלפת סיסמת DB

1. ב־Supabase: Database → Settings → Reset database password.
2. מעדכנים את הסיסמה בקובץ המקומי `.env.db-password`.
3. מעדכנים את `DATABASE_URL` ב־`.env.cloud.local` וב־Vercel. תווים מיוחדים בסיסמה חייבים להיות URL-encoded.
4. מבצעים Redeploy ובודקים את האתר ואת `document-worker`.

## בדיקה אחרי כל פריסה

- Deployment ב־Vercel הוא Ready.
- `document-worker` הוא Alive ואין שגיאות חוזרות בלוג.
- דף האתר נפתח.
- מסכי הממתינים והרשומות נקראים מה־DB.
- העלאת קובץ ניסוי, עיבוד, פיצול ידני, אישור, יצוא ומחיקה עובדים.
- בודקים Usage ב־Vercel, Supabase ו־Anthropic כדי לזהות חריגה מוקדם.

</div>
