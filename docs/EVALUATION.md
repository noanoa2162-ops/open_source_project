# תוצאות מדידה

**עדיין לא הורצה מדידה מול טבלת אמת מאומתת.**

הקובץ הזה נכתב מחדש אוטומטית בכל הרצה של `uv run evaluate --yes`, ואין לערוך בו מספרים ביד.

## איך מודדים

```powershell
uv run gold-draft          # טיוטת טבלת האמת (16 טפסים) נטענת למסך הבדיקה. כל שדה כתום.
uv run serve               # באמת מול התמונה, תיקון ו"אישור הטופס" לכל טופס
uv run evaluate --configs current,opus-medium,sonnet-high,haiku-high          # תוכנית ועלות משוערת, בלי קריאות
uv run evaluate --configs current,opus-medium,sonnet-high,haiku-high --yes    # הרצה בפועל
```
