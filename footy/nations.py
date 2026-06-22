"""National-team detection: is a typed name a country (→ national team) or a club?

This module only *classifies* a name — it fetches nothing. A name that matches a
country is routed to the national-team sources (Flashscore first, API-Football as
a fallback); everything else is treated as a club (WhoScored, then Flashscore).

It lives on its own — rather than inside ``apifootball`` — because both national
sources consult it, so housing it under one provider was misleading.
"""

from __future__ import annotations

import difflib
from typing import Optional

# Footballing nations, lower-cased, with the common alternate spellings a user
# might type. Membership here only decides "national team vs club" routing — the
# typed name itself is what gets sent to the provider's search. Organised by
# confederation to keep it maintainable; add new entries to the right block.
COUNTRIES = {
    # UEFA (Europe)
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belarus",
    "belgium", "bosnia and herzegovina", "bosnia", "bulgaria", "croatia",
    "cyprus", "czech republic", "czechia", "denmark", "england", "estonia",
    "faroe islands", "finland", "france", "georgia", "germany", "gibraltar",
    "greece", "hungary", "iceland", "ireland", "israel", "italy", "kazakhstan",
    "kosovo", "latvia", "liechtenstein", "lithuania", "luxembourg", "malta",
    "moldova", "montenegro", "netherlands", "north macedonia", "macedonia",
    "northern ireland", "norway", "poland", "portugal", "romania", "russia",
    "san marino", "scotland", "serbia", "slovakia", "slovenia", "spain",
    "sweden", "switzerland", "turkey", "ukraine", "wales",
    # CONMEBOL (South America)
    "argentina", "bolivia", "brazil", "chile", "colombia", "ecuador",
    "paraguay", "peru", "uruguay", "venezuela",
    # CONCACAF (North/Central America & Caribbean)
    "canada", "costa rica", "cuba", "curacao", "curaçao", "dominican republic",
    "el salvador", "guatemala", "guyana", "haiti", "honduras", "jamaica",
    "mexico", "nicaragua", "panama", "suriname", "trinidad and tobago",
    "united states", "usa",
    # CAF (Africa)
    "algeria", "angola", "benin", "botswana", "burkina faso", "burundi",
    "cameroon", "cape verde", "central african republic", "chad", "comoros",
    "congo", "dr congo", "congo dr", "djibouti", "egypt", "equatorial guinea",
    "eritrea", "eswatini", "ethiopia", "gabon", "gambia", "ghana", "guinea",
    "guinea-bissau", "ivory coast", "cote d'ivoire", "kenya", "lesotho",
    "liberia", "libya", "madagascar", "malawi", "mali", "mauritania",
    "mauritius", "morocco", "mozambique", "namibia", "niger", "nigeria",
    "rwanda", "senegal", "sierra leone", "somalia", "south africa",
    "south sudan", "sudan", "tanzania", "togo", "tunisia", "uganda", "zambia",
    "zimbabwe",
    # AFC (Asia)
    "afghanistan", "australia", "bahrain", "bangladesh", "bhutan", "brunei",
    "cambodia", "china", "hong kong", "india", "indonesia", "iran", "iraq",
    "japan", "jordan", "kuwait", "kyrgyzstan", "laos", "lebanon", "malaysia",
    "maldives", "mongolia", "myanmar", "nepal", "north korea", "oman",
    "pakistan", "palestine", "philippines", "qatar", "saudi arabia",
    "singapore", "south korea", "sri lanka", "syria", "tajikistan", "thailand",
    "turkmenistan", "united arab emirates", "uae", "uzbekistan", "vietnam",
    "yemen",
    # OFC (Oceania)
    "fiji", "new caledonia", "new zealand", "papua new guinea",
    "solomon islands", "tahiti", "vanuatu",
}


def is_national(name: str) -> bool:
    return (name or "").strip().lower() in COUNTRIES


def closest_country(name: str, cutoff: float = 0.8) -> Optional[str]:
    """Best-matching country for a (possibly misspelled) name, or None.

    Used to (a) auto-route obvious country typos to Flashscore ('Spein' ->
    'spain') and (b) suggest a correction in 'team not found' errors (looser
    cutoff). Returns the lower-cased country key from COUNTRIES.
    """
    matches = difflib.get_close_matches(
        (name or "").strip().lower(), list(COUNTRIES), n=1, cutoff=cutoff)
    return matches[0] if matches else None
