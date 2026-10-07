# Lokálny kontakt v Troubleshooting

Tlačidlo Kontakt číta `contact.json` z dátového adresára aplikácie, štandardne
`/data/contact.json`. Súbor sa načíta pri každom otvorení okna, bez reštartu.
Obsahuje reťazce `name`, `phone`, `email`. Chýbajúci alebo neplatný súbor sa
zobrazí ako informačná správa. Kontakt sa neukladá do receptu ani databázy.
Súbory s názvom `contact.json` sú ignorované Gitom.

Na zariadení aplikácie spustite ako používateľ, pod ktorým aplikácia beží:

```bash
python3 -c '
import json
from pathlib import Path
p = Path("/data/contact.json")
contact = {"name": input("Meno: "), "phone": input("Telefón: "), "email": input("E-mail: ")}
if not all(value.strip() for value in contact.values()):
    raise SystemExit("Všetky údaje musia byť vyplnené.")
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("w", encoding="utf-8") as f:
    p.chmod(0o600)
    json.dump(contact, f, ensure_ascii=False, indent=2)
print("Kontakt bol uložený.")
'
```

Údaje zadajte až na výzvy príkazu; nebudú súčasťou príkazu v histórii shellu.
Ak aplikácia používa iný dátový adresár, upravte cestu v príkaze.
Pri odmietnutí prístupu použite účet aplikácie s právom zápisu do dátového adresára.
