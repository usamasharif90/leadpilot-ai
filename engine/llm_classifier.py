
import re
COACH = re.compile(r"\b(coach(?:ing)?|mentor(?:ship)?|mastermind|1\s*:\s*1|book a call|apply now|program|academy|course|consultation)\b", re.I)
FACELESS = re.compile(r"\b(motivation|quotes?|compilation|clips|facts?|top\s*\d+|highlights)\b", re.I)
def classify_lead(title, description, recent_titles):
    text = " ".join([title or "", description or "", *list(recent_titles or [])])
    sells = bool(COACH.search(text))
    return {"sells_coaching": sells, "coaching_confidence": 0.78 if sells else 0.25,
            "faceless": "likely_faceless" if FACELESS.search(title or "") else "uncertain"}
