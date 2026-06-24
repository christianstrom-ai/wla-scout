"""Extrahera kontaktinfo (mejl + Instagram + "business inquiries") ur
kanalbeskrivning. Steg 4 i specen. Inga externa anrop — bara regex på det
vi redan har från channels_details."""
from __future__ import annotations
import re

EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
INSTAGRAM_AT = re.compile(r"(?:instagram(?:\.com)?[/:\s]+)?@([a-zA-Z0-9._]{2,30})")
INSTAGRAM_URL = re.compile(r"instagram\.com/([a-zA-Z0-9._]{2,30})", re.IGNORECASE)
BIZ = re.compile(r"(?:business|partnership|collab|inquir|sponsor|brand)[\s\S]{0,80}?([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", re.IGNORECASE)


def extract_contact(description: str | None) -> dict:
    if not description:
        return {"email": None, "instagram": None, "other": None, "found": False}
    biz_match = BIZ.search(description)
    email = (biz_match.group(1) if biz_match else None) or (EMAIL.search(description).group(0) if EMAIL.search(description) else None)
    ig = None
    m = INSTAGRAM_URL.search(description)
    if m:
        ig = m.group(1)
    if not ig:
        m = INSTAGRAM_AT.search(description)
        if m:
            ig = m.group(1)
    return {
        "email": email,
        "instagram": ig,
        "other": None,
        "found": bool(email or ig),
    }
