"""Closed list of sending entities, with the names they appear under on forms.

A sender that matches an alias is shown by its canonical name. Anything else
is kept exactly as read and flagged — an unknown sender is never guessed.
The list grows from the Ministry of Health institutions registry.
"""

from __future__ import annotations

import re

SENDERS: dict[str, tuple[str, ...]] = {
    "מאוחדת": ("מאוחדת", "קופת חולים מאוחדת", "קופ\"ח מאוחדת", "meuhedet"),
    "כללית": ("כללית", "שירותי בריאות כללית", "קופת חולים כללית", "clalit"),
    "מכבי": ("מכבי", "מכבי שירותי בריאות", "קופת חולים מכבי", "maccabi"),
    "לאומית": ("לאומית", "קופת חולים לאומית", "leumit"),
    "שמיר (אסף הרופא)": (
        "שמיר",
        "המרכז הרפואי שמיר",
        "מרכז רפואי שמיר",
        "אסף הרופא",
        "המרכז הרפואי שמיר (אסף הרופא)",
        "shamir",
        "assaf harofeh",
    ),
    "ברזילי": ("ברזילי", "המרכז הרפואי ברזילי", "מרכז רפואי ברזילי", "barzilai"),
    "שיבא": ("שיבא", "תל השומר", "המרכז הרפואי שיבא", "sheba"),
    "איכילוב": ("איכילוב", "סוראסקי", "המרכז הרפואי תל אביב", "ichilov"),
    "רמב\"ם": ("רמב\"ם", "רמבם", "הקריה הרפואית רמב\"ם", "rambam"),
    "הדסה": ("הדסה", "הדסה עין כרם", "הדסה הר הצופים", "hadassah"),
    "סורוקה": ("סורוקה", "המרכז הרפואי סורוקה", "soroka"),
    "בילינסון": ("בילינסון", "רבין", "המרכז הרפואי רבין", "beilinson", "rabin"),
    "אסותא אשדוד": (
        "אסותא אשדוד",
        "בית החולים הציבורי אסותא אשדוד",
        "בית החולים אסותא אשדוד",
        "assuta ashdod",
    ),
    "וולפסון": ("וולפסון", "המרכז הרפואי וולפסון", "wolfson"),
    "קפלן": ("קפלן", "המרכז הרפואי קפלן", "kaplan"),
    "מאיר": ("מאיר", "המרכז הרפואי מאיר", "meir"),
    "הלל יפה": ("הלל יפה", "המרכז הרפואי הלל יפה", "hillel yaffe"),
    "העמק": ("העמק", "המרכז הרפואי העמק", "emek"),
    "זיו": ("זיו", "המרכז הרפואי זיו", "ziv"),
    "פוריה": ("פוריה", "המרכז הרפואי פוריה", "poriya", "padeh"),
    "שערי צדק": ("שערי צדק", "המרכז הרפואי שערי צדק", "shaare zedek"),
    "לניאדו": ("לניאדו", "המרכז הרפואי לניאדו", "laniado"),
    "בני ציון": ("בני ציון", "המרכז הרפואי בני ציון", "bnai zion"),
    "כרמל": ("כרמל", "המרכז הרפואי כרמל", "carmel"),
    "נהריה": ("נהריה", "המרכז הרפואי לגליל", "galilee medical center"),
    "יוספטל": ("יוספטל", "המרכז הרפואי יוספטל", "yoseftal"),
}

_NOISE = re.compile(r"[\"'״׳().,\-–]")


def _key(text: str) -> str:
    return " ".join(_NOISE.sub(" ", text.lower()).split())


_ALIASES: dict[str, str] = {
    _key(alias): canonical for canonical, aliases in SENDERS.items() for alias in aliases
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
