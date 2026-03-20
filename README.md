# Oktoberfest Pinger 🍺

Monitora la disponibilità delle prenotazioni per il tendone **Augustiner Festhalle** all'Oktoberfest e ti avvisa via **WhatsApp** non appena aprono.

Gira **gratis 24/7** su GitHub Actions (cron ogni 10 minuti).

---

## Setup (5 minuti)

### 1. Attiva Callmebot su WhatsApp
1. Salva il numero **+34 644 80 01 90** in rubrica come "Callmebot"
2. Invia il messaggio: `I allow callmebot to send me messages`
3. Riceverai un messaggio con la tua **API key** (es. `1234567`)

### 2. Trova l'URL della pagina prenotazioni Augustiner
Vai sul sito ufficiale dell'Oktoberfest e cerca la pagina prenotazioni del tendone Augustiner Festhalle. Copia l'URL esatto.

Esempio: `https://www.oktoberfest.de/en/tents-rides/tents/augustiner-festhalle`

### 3. Crea il repo su GitHub
```bash
git init
git add .
git commit -m "Initial commit"
# Crea repo su github.com (pubblico per minuti illimitati), poi:
git remote add origin https://github.com/TUO_USERNAME/Oktoberfest_pinger.git
git push -u origin main
```

### 4. Aggiungi i GitHub Secrets
Su GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Nome               | Valore                              |
|--------------------|-------------------------------------|
| `WA_PHONE`         | Il tuo numero con prefisso (+39...) |
| `CALLMEBOT_APIKEY` | L'API key ricevuta da Callmebot     |
| `OKTOBERFEST_URL`  | URL della pagina da monitorare      |

### 5. Attiva le GitHub Actions
Vai su **Actions** nel repo → abilita i workflow se richiesto → clicca **"Run workflow"** per un test manuale.

---

## Come funziona

```
GitHub Actions (cron */10 * * * *)
    │
    ├── checkout repo (include state.json)
    ├── pip install
    ├── python checker.py
    │       │
    │       ├── GET pagina Oktoberfest (con User-Agent realistico + delay random)
    │       ├── Cerca keyword di prenotazione nel testo
    │       ├── Se DISPONIBILE e non ancora notificato → WhatsApp via Callmebot
    │       └── Aggiorna state.json
    │
    └── git commit state.json (solo se cambiato) → git push
```

### Anti-bot / Rate limit
- Delay casuale **1–4 secondi** prima di ogni richiesta
- **User-Agent** realistico (Chrome su Windows)
- Header `Accept-Language` e `Cache-Control` coerenti
- Una sola richiesta ogni **10 minuti** (molto sotto qualsiasi soglia di rate limit)
- In caso di **errore HTTP** (403/429): salva l'errore nello stato senza crashare
- Dopo **10 errori consecutivi** ti manda un WhatsApp di avviso

---

## Test locale

```bash
pip install -r requirements.txt

export WA_PHONE="+393331234567"
export CALLMEBOT_APIKEY="1234567"
export OKTOBERFEST_URL="https://..."

python checker.py
```
