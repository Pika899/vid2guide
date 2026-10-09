---
name: analyze-course
description: Trasforma una trascrizione grezza di ./transcriptions/ in una guida di studio strutturata in ./output_summary/, con indice cronologico dei momenti chiave, concetti tecnici del docente e prompt o framework mostrati a lezione. Usala quando una trascrizione è pronta e serve renderla studiabile e navigabile.
---

# Guida di studio da una trascrizione

L'input è `transcriptions/<nome>.txt`: una riga per segmento Whisper, nel formato
`[HH:MM:SS] testo`, preceduta da poche righe di header che iniziano con `#`.

L'output è `output_summary/<nome>.md`.

Lo scopo della guida non è riassumere per non riguardare il video: è **ritrovare in fretta
il punto giusto del video**. Ogni affermazione deve quindi portare con sé il timestamp da
cui viene. Una guida bella e senza riferimenti temporali ha mancato il suo scopo.

## 1. Inquadrare il file prima di leggerlo

Una lezione da 3-4 ore produce ~200-400 KB di testo. Non tentare di leggerla in un colpo
solo: rischi di troncare a metà e di costruire un indice cieco sull'ultima ora.

```bash
head -5 transcriptions/<nome>.txt     # header: video, modello, lingua, durata
wc -l transcriptions/<nome>.txt       # quanti segmenti
tail -3 transcriptions/<nome>.txt     # timestamp finale
```

Poi leggi con `Read` usando `offset` e `limit`, in blocchi di 400-600 righe, **in ordine**.
Alla fine di ogni blocco annota i momenti chiave trovati, con il loro timestamp, e passa al
successivo. Costruisci l'indice in modo incrementale: non tenere quattro ore di lezione in
testa per sintetizzarle tutte insieme alla fine.

## 2. Cosa cercare, blocco per blocco

Tre categorie, che corrispondono alle tre sezioni obbligatorie dell'output:

**Momenti chiave** — cambi di argomento, l'inizio di una demo, l'introduzione di uno
strumento, una domanda dal pubblico che apre una digressione lunga, un "adesso vi faccio
vedere". Sono i punti in cui uno studente vorrà saltare.

**Concetti tecnici** — le spiegazioni vere e proprie: cos'è una cosa, come funziona, perché
il docente la preferisce a un'alternativa. Registra la spiegazione *del docente*, con le sue
distinzioni, non la definizione da manuale che conosci già.

**Prompt e framework** — tutto ciò che è operativo e riutilizzabile: prompt dettati o
mostrati a schermo, schemi in N passi, checklist, criteri di scelta, nomi di strumenti e
impostazioni. È la parte a più alto valore pratico ed è quella che va trascritta con più
fedeltà.

## 3. Le regole che non si violano

**Ogni timestamp è copiato, mai calcolato o stimato.** Un `[01:47:30]` inventato manda lo
studente nel punto sbagliato di un video da quattro ore e distrugge la fiducia in tutta la
guida. Se non trovi il timestamp esatto di un passaggio, cita quello del segmento più vicino
che hai davvero letto.

**Niente riempimento.** Se la lezione non contiene prompt, la sezione "Prompt e framework"
resta vuota con la nota «nessuno mostrato in questa lezione». Non completare mai un concetto
accennato dal docente con la tua conoscenza generale sull'IA e non attribuirgliela: la guida
deve riportare cosa ha detto il docente, non cosa si sa sull'argomento. Se un'integrazione è
davvero utile, va in una sezione separata e dichiarata come tua.

**Gli errori di Whisper si segnalano, non si correggono in silenzio.** Il modello `base`
sbaglia con regolarità i nomi propri e i termini tecnici inglesi, che in un corso di IA sono
ovunque. Quando una parola è chiaramente storpiata, proponi la correzione marcandola:

> `[00:42:15]` Il docente introduce `[trascrizione incerta: "cianoftropia" → "chain of thought"?]`

Non silenziosamente "aggiustare": chi legge deve sapere che quella parola non è ciò che il
file conteneva.

**Artefatti tipici da riconoscere e non riportare come contenuto:**

- Frasi allucinate e ripetute sui silenzi lunghi (tipicamente «Sottotitoli e revisione a
  cura di…», «Grazie per aver guardato il video», ringraziamenti fuori contesto). Whisper le
  produce quando non c'è parlato: ignorale.
- Parole tagliate o duplicate ai confini dei chunk. La pipeline allinea i tagli ai silenzi
  proprio per ridurle, ma non le elimina del tutto. Il `manifest.json` in
  `temp_chunks/<slug>/` elenca gli offset esatti dei chunk: se una frase è strana, controlla
  se cade lì sopra.
- Ripetizioni immediate della stessa frase: quasi sempre un artefatto, non un'insistenza del
  docente.

### Errore noto e verificato: «Claude» trascritto «cloud»

Pronunciato in italiano, *Claude* è quasi indistinguibile da *cloud*, che è una parola
molto più frequente: Whisper ripiega su quella. Misurato su una trascrizione di prova di un corso su
Claude Code, nella versione migliore prodotta (`small` + `initial-prompt`): **51 occorrenze
su 61 sono sbagliate**. Non è un caso isolato, è la norma nel file.

Le forme in cui compare: `cloud`, `Cloud Code`, `clod`, e `clod.md` dove il docente sta
dicendo `CLAUDE.md`. Il termine si spezza anche tra due segmenti, con un timestamp in
mezzo — `...del nostro cloud [00:04:41] code...` — quindi non è cercabile con un'unica
espressione.

**Non correggere con una sostituzione globale.** Nel corso «cloud» compare anche nel suo
significato legittimo di cloud computing, per esempio *«la migrazione da locale a cloud»*.
Una sostituzione cieca corromperebbe quei passaggi.

La regola per la guida di studio:

1. Decidere **dal contesto**. Se si parla dello strumento — aprirlo, dargli istruzioni,
   un suo file di configurazione, un suo errore — è Claude. Se si parla di infrastruttura,
   hosting o migrazione di dati, è cloud.
2. Nella guida scrivere il termine **corretto**, senza marcare ogni singola occorrenza:
   marcarle tutte e cinquantuno renderebbe il documento illeggibile. Al posto di questo,
   mettere **una nota unica in testa alla guida** che dichiara la correzione applicata e
   la sua ragione. La trascrizione resta intatta come fonte fedele.
3. Marcare `[trascrizione incerta: ...]` **solo i casi davvero ambigui**, in cui il
   contesto non basta a decidere — per esempio *«dentro al mio account ora in cloud»* —
   e riportarli nella sezione «Da verificare» con il timestamp per riascoltarli.

## 4. Formato dell'output

Scrivi in `output_summary/<nome>.md`, stesso nome della trascrizione:

~~~~markdown
# <Titolo della lezione>

> Fonte: `transcriptions/<nome>.txt` — durata <H:MM>, modello whisper-<modello>.
> Ogni timestamp rimanda al video originale.

## In breve

Tre-cinque righe su cosa copre la lezione e a chi serve. Nessun timestamp qui.

## Indice cronologico

| Timestamp | Argomento |
|---|---|
| `[00:00:00]` | Introduzione e obiettivi |
| `[00:14:20]` | … |

Una riga per blocco tematico reale — non una ogni N minuti a tavolino. Su una lezione da
tre ore ci si aspetta grosso modo 15-30 voci.

## Concetti tecnici

### <Nome del concetto> `[HH:MM:SS]`

La spiegazione del docente, in 3-8 righe. Se ci torna sopra più avanti, aggiungi gli altri
timestamp in coda.

## Prompt e framework mostrati

### <Nome> `[HH:MM:SS]`

I prompt vanno in blocco di codice, riportati alla lettera:

```
<testo del prompt come dettato o mostrato>
```

Se il prompt è stato letto a voce e la trascrizione è incerta, dillo esplicitamente sotto
il blocco.

## Da verificare

Passaggi in cui la trascrizione è troppo rumorosa per essere affidabile, con il timestamp
per riascoltarli nel video. Se non ce ne sono, ometti la sezione.
~~~~

## 5. Prima di consegnare

- Ogni timestamp citato esiste davvero nel file di trascrizione (verificabile con `grep`)
- L'indice cronologico è in ordine crescente e nessuna voce supera la durata dell'header
- Nessun concetto presente nella guida ma assente dalla trascrizione
- Le incertezze sono marcate, non nascoste

Chiudi dicendo quante voci ha l'indice, quali sezioni sono rimaste vuote e perché, e quali
punti meritano un riascolto.
