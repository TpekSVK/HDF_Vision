# Lokálny kontakt v Troubleshooting

Na zariadení aplikácie v adresári projektu spustite:

```bash
bash docker/contact.sh set-info
```

Skript sa postupne opýta na meno, telefón a e-mail. Rovnakým príkazom môžete
kontakt neskôr zmeniť. Prázdne údaje ani zrušenie zadávania nezmenia pôvodný kontakt.
Skript používa Docker image `hdf_vision:dev`, rovnako ako `docker/security.sh`.
Ak image ešte neexistuje, najprv spustite `bash docker/build.sh`.

Údaje sa uložia iba do `/data/contact.json` na zariadení, mimo repozitára.
Zadané odpovede nie sú súčasťou príkazu v histórii shellu.
Kontakt sa načíta pri každom otvorení okna **Kontakt**, bez reštartu aplikácie.
Súbory s názvom `contact.json` sú ignorované Gitom.

Aplikácia číta kontakt z vlastného dátového adresára (štandardne `/data`).
Súbor obsahuje reťazce `name`, `phone`, `email`. Chýbajúci alebo neplatný súbor sa
zobrazí ako informačná správa. Kontakt sa neukladá do receptu ani databázy.
