"""Closed list of sending entities, with the names they appear under on forms.

A sender that matches an alias is shown by its canonical name. Anything else
is kept exactly as read and flagged — an unknown sender is never guessed.

Sources (checked 15/10/2026): the list of general hospitals in Israel on the
Hebrew wiki ("בתי חולים בישראל") and the Ministry of Health institutions
registry (registries.health.gov.il). Names that belong to more than one place
("האיטלקי" — Haifa and Nazareth; a bare "אסותא" — several branches) are left
out on purpose: they stay orange rather than being mapped to the wrong one.
"""

from __future__ import annotations

import re

SENDERS: dict[str, tuple[str, ...]] = {
    # ---- קופות החולים
    "כללית": ("כללית", "שירותי בריאות כללית", "קופת חולים כללית", "קופ\"ח כללית", "clalit"),
    "מכבי": ("מכבי", "מכבי שירותי בריאות", "קופת חולים מכבי", "קופ\"ח מכבי", "maccabi"),
    "מאוחדת": ("מאוחדת", "קופת חולים מאוחדת", "קופ\"ח מאוחדת", "meuhedet"),
    "לאומית": ("לאומית", "קופת חולים לאומית", "קופ\"ח לאומית", "leumit"),
    # ---- בתי חולים ממשלתיים
    "שיבא": (
        "שיבא", "תל השומר", "המרכז הרפואי שיבא", "המרכז הרפואי ע\"ש חיים שיבא",
        "המרכז הרפואי שיבא תל השומר", "sheba",
    ),
    "איכילוב": (
        "איכילוב", "סוראסקי", "המרכז הרפואי תל אביב", "המרכז הרפואי סוראסקי",
        "המרכז הרפואי תל אביב ע\"ש סוראסקי", "דנה דואק", "ichilov",
    ),
    "וולפסון": ("וולפסון", "המרכז הרפואי וולפסון", "המרכז הרפואי ע\"ש אדית וולפסון", "wolfson"),
    "הלל יפה": ("הלל יפה", "המרכז הרפואי הלל יפה", "hillel yaffe"),
    "רמב\"ם": ("רמב\"ם", "רמבם", "הקריה הרפואית רמב\"ם", "רמב\"ם הקריה הרפואית לבריאות האדם", "rambam"),
    "בני ציון": ("בני ציון", "המרכז הרפואי בני ציון", "מרכז רפואי בני ציון", "bnai zion"),
    "נהריה (המרכז הרפואי לגליל)": (
        "נהריה", "המרכז הרפואי לגליל", "המרכז הרפואי גליל", "galilee medical center",
    ),
    "זיו": ("זיו", "המרכז הרפואי זיו", "ziv"),
    "פוריה": ("פוריה", "המרכז הרפואי פוריה", "צפון פוריה", "המרכז הרפואי צפון", "ברוך פדה", "poriya", "padeh"),
    "ברזילי": ("ברזילי", "המרכז הרפואי ברזילי", "מרכז רפואי ברזילי", "barzilai"),
    "שמיר (אסף הרופא)": (
        "שמיר", "אסף הרופא", "המרכז הרפואי שמיר", "מרכז רפואי שמיר",
        "המרכז הרפואי שמיר (אסף הרופא)", "shamir", "assaf harofeh",
    ),
    # ---- בתי חולים של כללית
    "סורוקה": ("סורוקה", "המרכז הרפואי סורוקה", "המרכז הרפואי האוניברסיטאי סורוקה", "soroka"),
    "רבין (בילינסון / השרון)": (
        "רבין", "בילינסון", "גולדה השרון", "השרון פתח תקווה", "המרכז הרפואי רבין", "מרכז רפואי רבין",
        "beilinson", "rabin",
    ),
    "שניידר": ("שניידר", "מרכז שניידר לרפואת ילדים", "schneider"),
    "כרמל": ("כרמל", "המרכז הרפואי כרמל", "carmel"),
    "העמק": ("העמק", "המרכז הרפואי העמק", "emek"),
    "מאיר": ("מאיר", "המרכז הרפואי מאיר", "meir"),
    "קפלן": ("קפלן", "המרכז הרפואי קפלן", "kaplan"),
    "יוספטל": ("יוספטל", "המרכז הרפואי יוספטל", "yoseftal"),
    # ---- בתי חולים ציבוריים אחרים
    "הדסה": ("הדסה", "הדסה עין כרם", "הדסה הר הצופים", "מרכז רפואי הדסה", "hadassah"),
    "שערי צדק": ("שערי צדק", "המרכז הרפואי שערי צדק", "shaare zedek"),
    "לניאדו": ("לניאדו", "המרכז הרפואי לניאדו", "מרכז רפואי צאנז", "laniado"),
    "אסותא אשדוד": (
        "אסותא אשדוד", "בית החולים הציבורי אסותא אשדוד", "בית החולים אסותא אשדוד",
        "בית החולים האוניברסיטאי אסותא אשדוד", "assuta ashdod",
    ),
    "מעייני הישועה": ("מעייני הישועה", "מעיני הישועה", "המרכז הרפואי מעייני הישועה", "mayanei hayeshua"),
    "אוגוסטה ויקטוריה": ("אוגוסטה ויקטוריה", "מוטלע", "augusta victoria"),
    "סנט ג'וזף": ("סנט ג'וזף", "הצרפתי ירושלים", "st joseph"),
    "אל-מקאסד": ("אל-מקאסד", "אל מקאסד", "מקאסד", "makassed"),
    "האנגלי (נצרת)": ("בית החולים האנגלי", "בית חולים נצרת", "האנגלי נצרת", "emms"),
    "המשפחה הקדושה (נצרת)": ("המשפחה הקדושה", "holy family"),
    "הצרפתי (נצרת)": ("סן ונסן דה פול", "הצרפתי נצרת", "st vincent de paul"),
    "האיטלקי (חיפה)": ("האיטלקי חיפה", "בית החולים האיטלקי חיפה"),
    # ---- בתי חולים פרטיים
    "אסותא רמת החייל": ("אסותא רמת החייל", "assuta ramat hahayal"),
    "הרצליה מדיקל סנטר": ("הרצליה מדיקל סנטר", "herzliya medical center"),
    "אלישע": ("אלישע", "בית החולים אלישע", "elisha"),
    "משגב לדך": ("משגב לדך", "misgav ladach"),
}

_NOISE = re.compile(r"[\"'״׳().,\-–]")


def _key(text: str) -> str:
    return " ".join(_NOISE.sub(" ", text.lower()).split())


# אותו מוסד נכתב עם ה"א הידיעה ובלעדיה: "המרכז הרפואי מאיר" / "מרכז רפואי מאיר".
_SPELLINGS = (("המרכז הרפואי", "מרכז רפואי"), ("בית החולים", "בית חולים"))


def _variants(alias: str) -> set[str]:
    found = {alias}
    for formal, plain in _SPELLINGS:
        for name in list(found):
            found |= {name.replace(formal, plain), name.replace(plain, formal)}
    return found


_ALIASES: dict[str, str] = {
    _key(variant): canonical
    for canonical, aliases in SENDERS.items()
    for alias in (canonical, *aliases)
    for variant in _variants(alias)
}


def canonical_sender(text: str) -> str | None:
    """The canonical name for a sender as read off a form, or None if unknown.

    Exact alias match first; then a known alias appearing as a whole phrase
    inside a longer printed name ("המרכז הרפואי שמיר (אסף הרופא) מוסמך..."),
    longest alias first so "המרכז הרפואי שמיר" wins over "שמיר".

    Short single words ("זיו", "מאיר", "כרמל") are ordinary words too, so they
    only match exactly: "מרפאת זיו" must stay unknown and orange, not quietly
    become Ziv hospital.
    """
    key = _key(text)
    if not key:
        return None
    if key in _ALIASES:
        return _ALIASES[key]
    padded = f" {key} "
    for alias in sorted(_ALIASES, key=len, reverse=True):
        distinctive = " " in alias or len(alias) >= 5
        if distinctive and f" {alias} " in padded:
            return _ALIASES[alias]
    return None


def senders_for_prompt() -> str:
    """The closed list as the models see it: one canonical name per line."""
    return "\n".join(f"- {name}" for name in SENDERS)
