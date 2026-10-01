# Social Transcript

Lokal prototyp: **mikrofon → Pianissimo → arbetsmall via Ollama → granskning och ny version**.

BBIC, IBIC, ASI, FREDA och ESTHER finns som val. Knappen **Överför till Lifecare** är avstängd och har ingen funktion, ingen anslutning och inget API bakom sig.

> Använd påhittade uppgifter under test. Detta är ett dokumentationsstöd för utkast, inte ett godkänt verksamhetssystem, bedömningsinstrument eller beslutsstöd.

## Starta på Windows

Du behöver Python 3.11 (64-bit), Git och lokal Ollama. Ingen Node-installation, API-nyckel, Docker, CUDA eller FFmpeg behövs för att köra appen.

Öppna PowerShell:

```powershell
git clone https://github.com/Jtensetti/social-transcript.git
cd social-transcript
.\setup.cmd
.\start.cmd
```

`setup.cmd` skapar en separat Python-miljö, installerar beroenden och hämtar **KlangAI/pianissimo-sv-onnx, int8**, cirka 660 MB. Det är Klangs officiella ONNX-version av Pianissimo; inte en annan talmodell. Hämtningen behöver internet. Modeller hämtas aldrig automatiskt när ett samtal bearbetas.

**Öppna `http://127.0.0.1:8765` i Chrome eller Edge.** Låt terminalfönstret vara öppet. Nästa gång räcker `start.cmd`. Ctrl+C stoppar servern.

Starta även Ollama. `gemma4:e4b` är förvalt när det finns installerat. Andra nedladdade lokala modeller går att välja i gränssnittet. Ingen modell hämtas via webbgränssnittet. Vid behov:

```powershell
ollama list
# Endast om modellen saknas:
ollama pull gemma4:e4b
```

Modellvikterna har egna licenser. Kontrollera modellkortet för en annan modell innan den används.

### Linux och macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py --download-model
python app.py
```

ONNX-versionen använder CPU även på en dator med GPU. Ingen särskild GPU krävs av prototypen; faktisk hastighet och minnesåtgång måste provas på den dator som ska användas. Ollama väljer själv tillgänglig hårdvara.

## Flödet

1. Välj **arbetsmall**. Klicka på **Hitta mikrofoner**, tillåt mikrofonen och välj önskad enhet.
2. Klicka **Start**. Klicka sedan **Transkribera**: inspelningen stoppas, Pianissimo transkriberar och vald lokal språkmodell fyller mallen. Det går också att stoppa först och lyssna på inspelningen.
3. Granska och redigera transkriberingen och mallens fält. Lägg till **Kontext och justeringar** och klicka **Skapa ny version**. Tidigare versioner, inklusive manuella textändringar, finns kvar i listan. Varje genererad version behåller sitt ursprungliga textunderlag och sin kontext.

Du kan även klistra in en text och skapa utkast utan mikrofon eller Pianissimo. Om Ollama inte är igång kan ljudet ändå transkriberas; utkastet kan skapas efter att Ollama startats.

Sakuppgifter som behöver rättas bör rättas i transkriberingen eller kompletteras i kontexten. Ett tidigare utkast är inte en ny fristående faktakälla för modellen. Att ändra ett textfält garanterar därför inte att en ny version behåller en faktauppgift som saknar stöd i underlaget.

**Kopiera text** kopierar på uttrycklig begäran ett utkast till systemets urklipp. Det är inte en Lifecare-integration. **Nytt samtal** rensar appens ljud, text, kontext och versioner efter bekräftelse. Versionshistoriken finns endast i den öppna sidan; ingen databas eller automatisk filhistorik används.

## Om arbetsmallarna

Mallarnas fältnamn och skrivinstruktioner ligger i `templates.json`. De är **förenklade, egna arbetsmallar** inspirerade av nedanstående arbetssätt – inte fullständiga eller godkända originalformulär. De behöver ersättas eller anpassas till verksamhetens faktiska dokumentationsmallar före ett riktigt införande.

- **BBIC:** barnets berättelse, utveckling, föräldrarnas förmåga, familj och miljö samt uttryckligen överenskommet fortsatt arbete. Inte en fullständig BBIC-utredning.
- **IBIC:** individens perspektiv, vardag, resurser, uttalade behov och mål samt angivna insatser. Ingen automatisk ICF-kodning eller behovsbedömning.
- **ASI:** samtalsanteckning utifrån de sju livsområdena. Inte originalintervjun eller ASI Grund/Uppföljning. Inga automatiska skattningar eller kompositpoäng.
- **FREDA:** anteckningsstöd om personens berättelse, beskrivet våld, trygghet, barn, stöd och överenskommen uppföljning. Inga riskpoäng, risknivåer eller egna säkerhetsplaner. Ersätter inte något av FREDA-instrumenten.
- **ESTHER:** personcentrerad anteckning om vad som är viktigt för personen, resurser, önskemål och samordning. ESTHER är här tolkningen av beställningen; detta avser **inte ESTER-bedömning** för barn och unga.

Modellen instrueras att lämna fält tomma när uppgifter saknas, skilja utsagor från observationer och flagga oklarheter. **Det är instruktioner, inte en garanti mot hallucinationer eller utelämnanden.** JSON-valideringen kontrollerar format och fält, inte om innehållet är sant.

## Lokal behandling och avgränsningar

Servern binds till `127.0.0.1`. Ljud skickas först när **Transkribera** klickas och tas emot som WAV direkt i minnet, utan temporär ljudfil. Web Audio/AudioWorklet fångar mono PCM16; servern samplar om vid behov. Pianissimo laddas från den lokala modellmappen med Hugging Face i offline-läge. Ollama anropas endast på `http://127.0.0.1:11434`; miljöproxy och HTTP-omdirigeringar används inte.

Appen visar bara nedladdade modellposter med lokal storlek, filtrerar molnnamn och kontrollerar fjärrmetadata med `/api/show` före ett modellanrop. Detta ersätter inte en kontroll av Ollamas konfiguration: **stäng av Ollamas molnfunktioner och begränsa utgående trafik** i en framtida känslig driftsmiljö. En komprometterad eller felkonfigurerad lokal modellserver kan inte göras säker enbart av denna klient.

Inga analysverktyg, externa typsnitt, CDN:er, webbsökningar, telemetrianrop från appen, localStorage eller service workers. API-svar cachelagras inte. Host/Origin-kontroller, anropstoken för serverinstansen och restriktiv Content Security Policy ingår. Serverns accesslogg är avstängd och felmeddelanden återger inte promptinnehåll.

Detta är fortfarande en **en-användarprototyp utan inloggning, ärendeisolering, revisionslogg eller formell informationssäkerhetsgranskning**. Exponera den inte på internet eller kommunens nätverk. Datorns växlingsfil, kraschdumpar, webbläsartillägg, Ollamas egna loggar och urklippshistorik ligger utanför appens kontroll. Att rensa sidan är inte säker överskrivning av RAM eller OS-lagring. Samtal kräver verksamhetens rutiner, rättsliga förutsättningar och godkända arbetssätt före verklig användning.

### Prototypens gränser

Max **15 minuter per inspelning**. Ljudet delas i sammanhängande segment om högst 25 sekunder med klippning vid en låg ljudnivå nära segmentets slut; inga sampel hoppas över. Klippgränser kan ändå påverka igenkänningen. Ingen talarseparering, talaridentifiering eller ordnivå-koppling mellan text och ljud är implementerad. Överlappande tal, namn, negationer och siffror kräver extra granskning.

Ollama får ett 32 768-tokenfönster. Ett konservativt bytebaserat storlekstak avvisar för långa kombinationer av transkribering, kontext och tidigare utkast **i stället för att medvetet kapa underlaget**. Långa inspelningar kan därför behöva bearbetas i mindre delar. En bearbetning i taget tillåts. Modellfel eller felaktig JSON ersätter inte ett befintligt utkast. Ingen automatisk signering, journalåtkomst, patientmatchning eller överföring finns.

## Felsökning

**Pianissimo behöver hämtas:** kör `setup.cmd` eller `.venv\Scripts\python.exe app.py --download-model`. Vikterna finns i `models/pianissimo-sv-onnx/`, som ignoreras av Git. En `download.json` i modellmappen registrerar modellens hämtade revision. Flyttas mappen kan `PIANISSIMO_MODEL_DIR` sättas till en lokal sökväg före start.

**Ollama är inte anslutet:** starta Ollama och klicka **Uppdatera**. Kontrollera `ollama list`. Ollama ska lyssna på standardport 11434 på samma dator. Förvalt modellnamn kan ändras med miljövariabeln `OLLAMA_MODEL` före start.

**Modellen följer inte strukturen:** prova igen, korta underlaget eller välj en annan lokal instruktionsmodell. En ny modell behöver testas med svenska syntetiska exempel. Inget fritt, ovalidierat textsvar används som tyst reservlösning.

**Mikrofonen saknas:** använd localhost-adressen, kontrollera mikrofonbehörigheten i både webbläsaren och Windows och välj enheten på nytt. Organisationspolicy kan blockera mikrofonen även om användaren trycker Tillåt.

## Utveckling och tester

Python/FastAPI + vanlig HTML/CSS/JavaScript. Ingen frontendbyggkedja.

```bash
python -m pip install -r requirements-core.txt pytest
python -m pytest -q
# Valfritt, Node 22+ behövs bara för dessa tester:
node --test tests/recorder.test.mjs
```

API-testerna mockar Ollama och ASR där verkliga modellvikter annars skulle krävas. Ljudvalidering, omsampling, segmentering, mallstruktur, fjärrmodellblockering, revisionsunderlag och felhantering testas utan modeller. Node-testerna kör den faktiska PCM-worklet-koden och WAV-kodningen med syntetiska sampel.

Valfritt webbläsartest, **med servern redan igång**:

```bash
python -m pip install playwright
python -m playwright install chromium
python tests/browser_smoke.py
```

Det testet använder webbläsarens syntetiska mikrofon och simulerade modellsvar. Det kräver att webbläsarens policy tillåter localhost, testmikrofon och urklipp. Det verifierar inte Pianissimos eller Gemmas transkriptions-/skrivkvalitet. Testa alltid den verkliga kedjan med påhittade svenska samtal på måldatorn: saknade uppgifter, motsägande uppgifter, datum, negationer, namn, dialekt och längre inspelningar.

## Källor och erkännanden

Pianissimo är utvecklad av **Klang AI AB** och publicerad under **CC BY 4.0**. Appen använder Klangs publicerade int8 ONNX-export; vi har inte modifierat eller tränat om modellvikterna. Modellvikter distribueras inte med repot.

- Pianissimo: https://huggingface.co/KlangAI/pianissimo-sv
- Officiell ONNX-export: https://huggingface.co/KlangAI/pianissimo-sv-onnx
- onnx-asr: https://github.com/istupakov/onnx-asr
- CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
- Gemma 4 e4b i Ollama: https://ollama.com/library/gemma4:e4b
- Ollama API och strukturerade svar: https://docs.ollama.com/api/chat
- Ollama molnfunktioner: https://docs.ollama.com/cloud
- BBIC: https://www.socialstyrelsen.se/kunskapsstod-och-regler/omraden/barn-och-unga/barn-och-unga-i-socialtjansten/barns-behov-i-centrum/
- IBIC: https://www.socialstyrelsen.se/kunskapsstod-och-regler/omraden/individens-behov-i-centrum-ibic/
- ASI: https://www.socialstyrelsen.se/kunskapsstod-och-regler/omraden/evidensbaserad-praktik/metodguiden/asi-addiction-severity-index/
- FREDA: https://www.socialstyrelsen.se/kunskapsstod-och-regler/omraden/evidensbaserad-praktik/metodguiden/freda/
- ESTHER: https://www.rjl.se/qulturum/natverka/esther/

Namn och referenser innebär inte att lösningen är granskad eller godkänd av metodernas upphovspersoner eller myndigheter.
