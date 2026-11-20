# CLAUDE.md – OV-vertragingsvoorspeller

Dit bestand is de vaste briefing voor Claude Code. Lees het volledig voordat je iets doet, en houd de sectie Status onderaan bij na elke afgeronde taak.

## 1. Wie ben ik en hoe werken we samen

- Ik ben een starter die dit bouwt als portfolioproject voor mijn cv (richting data engineering / data science). Ik leer tijdens het bouwen.
- Communiceer in het Nederlands. Code, commentaar in code, commit-berichten en bestandsnamen mogen Engels of Nederlands zijn, maar wees consistent per bestand (bestaande code gebruikt Nederlandse docstrings/logberichten en Engelse namen).
- Leg uit wat je doet en waarom, kort en concreet, zodat ik het in een sollicitatiegesprek kan navertellen. Na elke taak: een korte samenvatting "wat is er gebouwd, hoe test ik het zelf, wat leer ik hiervan".
- Ik gebruik VS Code met een virtuele omgeving in `.venv`. Geef commando's voor zowel Windows (PowerShell) als macOS/Linux als ze verschillen.
- Vraag eerst voordat je: iets globaal installeert, betaalde diensten gebruikt, API-keys nodig hebt, bestanden verwijdert, of afwijkt van de architectuur hieronder.
- Werk in kleine stappen. Liever vijf kleine, geteste commits dan één grote.

## 2. Het project

Een end-to-end systeem dat voorspelt hoe groot de kans is dat een trein in Nederland ≥ 5 minuten vertraging heeft bij vertrek, op basis van historische treindata en het weer.

Eindresultaat: een live web-app + API, een geautomatiseerde datapipeline met tests, een dashboard met inzichten, en een nette GitHub-repo met README die recruiters overtuigt.

### Architectuur

```
Bronnen                    Opslag                 Transformatie         Gebruik
Rijden de Treinen ──┐
KNMI (weer/uur)  ───┼──► data/raw/ ──► DuckDB raw.* ──► dbt staging ──► dbt marts ──► dashboard (Streamlit)
NS API (live)    ───┘                                                              ├► ML-model (LightGBM)
                                                                                   └► FastAPI + web-app
Dagster orkestreert dagelijks · GitHub Actions test elke push · Docker voor deployment
```

### Tech stack (niet zonder overleg wijzigen)

| Onderdeel | Keuze |
|---|---|
| Taal | Python ≥ 3.11 |
| Database | DuckDB (bestand `data/ov.duckdb`) |
| Transformaties | dbt-core + dbt-duckdb (map `transform/`) |
| Orkestratie | Dagster + dagster-dbt (map `orchestration/`) |
| Analyse | Jupyter notebooks (map `notebooks/`), polars of pandas |
| Model | scikit-learn, LightGBM, SHAP, MLflow (lokaal) |
| API / app | FastAPI (`api/`), Streamlit (`app/`) |
| Kwaliteit | pytest, ruff (lint + format), dbt tests |
| CI/CD | GitHub Actions |
| Deployment | Docker + docker-compose; hosting op Render / Fly.io / Hugging Face Spaces (overleggen) |

## 3. Databronnen

### Rijden de Treinen – treinarchief (hoofdbron)

- Licentie CC BY 4.0: bron altijd vermelden in README en dashboard.
- Info: https://www.rijdendetreinen.nl/open-data/treinarchief
- Maandbestanden: `https://opendata.rijdendetreinen.nl/public/services/services-YYYY-MM.csv.gz` (volgens de site vanaf 2023-01). Jaarbestanden: `services-YYYY.csv.gz` (vanaf 2019).
- Formaat: CSV, gzip. Eén rij = één stop van één rit. Elke rit heeft ≥ 2 rijen.
- Gedocumenteerde kolommen (zie `COLUMN_MAP` in `src/ov_vertraging/config.py`): Service:RDT-ID, Service:Date, Service:Type, Service:Company, Service:Train number, Service:Completely cancelled, Service:Partly cancelled, Service:Maximum delay, Stop:RDT-ID, Stop:Station code, Stop:Station name, Stop:Arrival time, Stop:Arrival delay, Stop:Arrival cancelled, Stop:Departure time, Stop:Departure delay, Stop:Departure cancelled, Stop:Platform change, Stop:Planned platform, Stop:Actual platform
- Tijden zijn RFC 3339 (met tijdzone). Vertragingen in minuten. Lege velden = niet van toepassing (bv. geen aankomst op beginstation).
- Ook beschikbaar: stationsbestand (code, naam, coördinaten, categorie) en storingsarchief (sinds 2011) via https://www.rijdendetreinen.nl/open-data

### KNMI – weer (fase 2)

- KNMI Data Platform (https://dataplatform.knmi.nl), gratis API-key nodig.
- Nodig: uurwaarden per weerstation (temperatuur, neerslag, windsnelheid, windstoten, eventueel sneeuw/zicht) + coördinaten van de weerstations.
- Onderzoek zelf welke dataset/endpoint hiervoor het geschiktst is, leg de keuze aan mij voor en documenteer die in de README.
- Voor voorspellingen in fase 5 is een weersverwachting nodig; overleg de bron (KNMI of een gratis alternatief zoals Open-Meteo).

### NS Reisinformatie API (fase 5)

- Key via https://apiportal.ns.nl (product "Ns-App", Reisinformatie API).
- Header: `Ocp-Apim-Subscription-Key`. Gebruik voor live vertrektijden per station.
- Respecteer rate limits; cache responses.

## 4. Werkafspraken en conventies

- Projectstructuur: `src/`-layout, package `ov_vertraging`. Scripts draaien als `python -m ov_vertraging.<module>`.
- Nooit committen: `data/`, `*.duckdb`, `.venv/`, `.env`, modellen > 50 MB, API-keys. Check `.gitignore` bij elke nieuwe soort bestand.
- Secrets in `.env` (lezen met python-dotenv), plus een `.env.example` zonder waarden.
- Idempotent: elke stap moet veilig opnieuw gedraaid kunnen worden (downloads overslaan als ze al bestaan, tabellen `CREATE OR REPLACE` of incrementeel met duidelijke sleutel).
- Tests: elke nieuwe functie met logica krijgt een pytest-test. Gebruik kleine fixtures in `tests/fixtures/` of genereer ze in de test — nooit afhankelijk van internet of de echte grote data.
- Kwaliteit vóór elke commit: `ruff check . --fix`, `ruff format .`, `pytest` (en vanaf fase 2 `dbt build`). Alles groen, anders niet committen.
- Git-werkwijze: per fase een branch (`fase-2-pipeline`, `fase-3-analyse`, …), kleine commits in de gebiedende wijs ("Voeg KNMI-download toe"), afronden met een pull request met een duidelijke beschrijving. Push niet naar main zonder dat ik het weet.
- Geheugen: werk met SQL in DuckDB voor zware aggregaties; laad nooit alle ruwe rijen in pandas.
- Dependencies toevoegen in `pyproject.toml` (runtime vs. `[dev]`), niet los.
- README bijwerken aan het einde van elke fase (roadmap-vinkjes, hoe te draaien, screenshots/inzichten).

## 5. Het plan per fase

Elke fase heeft een "Klaar als"-lijst. Een fase is pas af als alles daarvan klopt én ik het zelf heb kunnen draaien.

### Fase 1 – Data binnenhalen (code bestaat, nog niet getest op echte data)

Bestaand: `config.py`, `download.py`, `load.py`, `tests/test_pipeline.py`, README, `pyproject.toml`, `.gitignore`. Taken:

1. Help mij de venv maken en `pip install -e ".[dev]"` draaien; pytest moet groen zijn.
2. Download één maand (`python -m ov_vertraging.download --start 2026-10 --end 2026-10`) en inspecteer het echte bestand: header (let op spaties/kolomnamen), voorbeeldrijen, tijdformaat, waarden van booleans, grootte, aantal rijen.
3. Pas `COLUMN_MAP`, `TYPES` en de loader aan als de echte data afwijkt van de docs. Maak de test-fixture gelijk aan het echte formaat (paar echte regels, geanonimiseerd is niet nodig — het is open data).
4. Draai `python -m ov_vertraging.load` en controleer de samenvatting op logica (realistische percentages? tijdzones goed?).
5. Voeg datakwaliteitschecks toe aan de loader of als test: aantal rijen > 0, geen duplicaat stop_id, departure_delay_min binnen een redelijk bereik, % NULL per kolom loggen.
6. Download daarna meer maanden (minimaal 12 voor fase 4) en meet laadtijd en DB-grootte; noteer die in de README.
7. Voeg het stationsbestand toe (download + `raw.stations`).
8. Git: `git init`, eerste commit, en help mij de GitHub-repo aanmaken (via `gh repo create` als de GitHub CLI er is, anders stap voor stap via github.com). Repo public, beschrijving en topics zetten, MIT-licentie toevoegen.

**Klaar als:** tests groen · ≥ 12 maanden in `raw.services` · `raw.stations` gevuld · README bijgewerkt · repo staat op GitHub zonder data-bestanden.

### Fase 2 – Datapipeline (branch `fase-2-pipeline`)

- KNMI-weer: module `download_weather.py` + laden in `raw.weather_hourly` en `raw.weather_stations`. API-key uit `.env`.
- dbt-project in `transform/` met profiel naar `../data/ov.duckdb`:
  - `sources.yml` voor alle `raw.*` tabellen.
  - staging: `stg_services` (opschonen, tijden naar Europe/Amsterdam, afgeleide velden departure_hour, weekday), `stg_stations`, `stg_weather`.
  - intermediate: `int_station_weather_map` (elk treinstation → dichtstbijzijnde weerstation via haversine), `int_departures_with_weather` (elk vertrek + weer van dat uur + volgend station zodat een traject ontstaat).
  - marts: `fct_departures` (één rij per vertrek, met target `is_delayed_5min`), `dim_station`, `dim_date` (incl. Nederlandse feestdagen en schoolvakanties indien haalbaar), `agg_route_hour` (punctualiteit per traject × uur × weekdag).
  - Tests: unique/not_null op sleutels, accepted_values op service_type, relationships tussen fct en dims, eigen test: vertraging tussen −5 en 600 min. Minimaal 10 tests.
  - `dbt docs generate` moet werken (lineage-grafiek).
- Dagster in `orchestration/`: assets voor treindownload, weerdownload, laden en alle dbt-modellen (dagster-dbt); een dagelijks schema dat de nieuwe maand/dag ophaalt. `dagster dev` moet de volledige lineage tonen. Maak een screenshot voor de README (vraag mij dat te doen als jij het niet kunt).
- Leg in de README uit waarom drie lagen, en wat idempotent betekent in deze pipeline.

**Klaar als:** `dbt build` volledig groen · Dagster-UI toont de hele pipeline · schema staat aan · README heeft lineage-screenshot.

### Fase 3 – Analyse & dashboard (branch `fase-3-analyse`)

- Notebook `notebooks/01_verkenning.ipynb`, werkend op de marts. Beantwoord met elk één grafiek + één zin conclusie:
  - slechtste uur en weekdag; seizoenseffect (herfst?)
  - minst betrouwbare trajecten (met minimum aantal ritten)
  - Intercity vs. Sprinter
  - effect van wind ≥ 7 Bft, vorst, hevige neerslag op de kans op vertraging
  - doorwerking: hoe vaak groeit 3 min vertraging uit tot ≥ 10 min
  - de 5 slechtste dagen en (opzoeken) wat er die dag gebeurde
- Streamlit-dashboard `app/dashboard.py`: KPI-tegels, kaart van stations gekleurd op punctualiteit, heatmap uur × weekdag, grafiek vertraging vs. weer, filter op periode en treintype. Bronvermelding Rijden de Treinen zichtbaar.
- Optioneel: export van marts naar Parquet voor Power BI.
- Top 3 inzichten in de README.

**Klaar als:** notebook draait van boven naar beneden zonder fouten · `streamlit run app/dashboard.py` werkt · README heeft inzichten + screenshot.

### Fase 4 – Model (branch `fase-4-model`)

- Probleemdefinitie in `docs/model.md`: target `is_delayed_5min`, voorspelmoment een paar uur vóór vertrek, alleen features die dan bekend zijn.
- Features: traject, station, treintype, uur, weekdag, maand, feestdag/vakantie, weer van dat uur (in training gemeten waarden; noteer dat dit in productie een verwachting is), historische punctualiteit van traject × uur berekend uit alleen eerdere data, drukte (aantal geplande vertrekken in dat uur).
- Leakage voorkomen: NIET gebruiken: vertraging van dezelfde rit op eerdere stops, service_max_delay_min, annuleringsvelden van dezelfde rit, of iets uit de toekomst. Schrijf een test die controleert dat verboden kolommen niet in de featureset zitten.
- Split op tijd (bv. train jan–jul, validatie aug, test sep–okt). Nooit random.
- Modellen: baseline (historisch % per traject × uur) → logistische regressie → LightGBM (categorische features, kleine hyperparameter-search op de validatieset).
- Evaluatie op de testset: PR-AUC, ROC-AUC, Brier-score, kalibratiecurve, en resultaten per treintype en per maand. SHAP-samenvatting. Foutanalyse (waar zit het model ernaast; storingen zijn onvoorspelbaar — benoem dat eerlijk).
- MLflow lokaal voor alle runs. Beste model opslaan in `models/` (gitignored) met een klein metadata-bestand (features, versie, datum, scores).
- Code in `src/ov_vertraging/model/` (`features.py`, `train.py`, `evaluate.py`), aanroepbaar als `python -m ov_vertraging.model.train`.

**Klaar als:** resultaatentabel baseline vs. modellen in `docs/model.md` en README · SHAP- en kalibratiegrafiek · reproduceerbare training met één commando.

### Fase 5 – Live (branch `fase-5-live`)

- FastAPI in `api/`: `GET /health`, `POST /predict` (input: van-station, naar-station, vertrektijd → output: kans + korte uitleg/top-factoren), `GET /departures/{station}` (live vertrekken via NS API met per trein de voorspelde kans). Pydantic-schema's, tests met TestClient.
- Weersverwachting ophalen voor het voorspelmoment (bron eerst met mij overleggen).
- Streamlit uitbreiden met tab "Voorspel mijn reis" die de API aanroept.
- Docker: Dockerfile voor API en app, `docker-compose.yml` die beide start.
- GitHub Actions `.github/workflows/ci.yml`: ruff, pytest, dbt build op een kleine meegeleverde sample-dataset. Badge in README.
- Hosting: opties met kosten/beperkingen aan mij voorleggen, dan samen uitrollen.
- Monitoring: voorspellingen loggen en dagelijks vergelijken met de werkelijkheid (eenvoudig: een Dagster-asset + grafiek in het dashboard).

**Klaar als:** publieke link werkt voor een ander · CI groen · README toont link + badge.

### Fase 6 – Presenteren

README definitief maken: één-zin-pitch + live-link bovenaan, demo-GIF, architectuurplaatje, lineage-screenshot, top-inzichten, modelresultaten, "zelf draaien"-instructies, bronvermelding. Daarnaast help je mij met een concept voor een LinkedIn-post en drie cv-bullets met echte cijfers uit het project.

## 6. Doelstructuur van de repo (eindsituatie)

```
ov-vertraging/
├── CLAUDE.md  README.md  pyproject.toml  .gitignore  .env.example  LICENSE
├── docker-compose.yml
├── .github/workflows/ci.yml
├── src/ov_vertraging/      config.py download.py load.py download_weather.py
│   └── model/              features.py train.py evaluate.py
├── transform/              dbt-project (models/staging, intermediate, marts; tests)
├── orchestration/          Dagster-definities
├── notebooks/              01_verkenning.ipynb …
├── app/                    dashboard.py (Streamlit)
├── api/                    main.py (FastAPI) + Dockerfile
├── docs/                   model.md, afbeeldingen voor README
├── tests/                  pytest + fixtures
└── data/  models/          (gitignored)
```

## 7. Status

Werk dit bij na elke afgeronde taak (datum, wat gedaan, wat volgende stap).

- 2026-11-20 — Fase 1 code opgezet (download, loader, 2 tests). Getest met nepbestand in gedocumenteerd formaat; nog niet gedraaid op echte data. Volgende stap: Fase 1, taak 1–8.
- 2026-11-20 — Map bleek leeg; Fase 1-code opnieuw opgebouwd op basis van het échte bestand services-2026-10. Taak 1 af (`.venv` op Python 3.14, 7 tests groen, ruff schoon). Taak 2 af: oktober gedownload (35 MB gz) en geladen: 1.951.428 stops, 213.763 ritten, laadtijd 1,5 s, DB 40 MB, 4,9% vertrekken ≥ 5 min. Header = docs, dus COLUMN_MAP ongewijzigd; perrons zijn tekst ("15a", "1/2"). Bevindingen voor later:
  - Geen negatieve vertragingen (te vroeg = 0).
  - 197 stops met ~1440 min vertraging, allemaal DB (Duitse treinen): datumfout van precies 24 uur → wegfilteren in staging.
  - `service_max_delay_min` staat (bijna) alleen op de eerste stop van een rit, rest 0 → niet als per-stop-waarde gebruiken (en sowieso leakage).
  - 11% NULL vertrektijd = eindstations (verwacht).
  - Tijdzones kloppen: +02 en +01 (zomertijd eindigde 25 okt).
  - Ook bus-/metrovervanging (`... ipv trein`, `Bus`) en buitenlandse treinen in de data → filteren in staging.
  - Eerste git-commit gemaakt (lokaal, branch main).
- 2026-11-20 — Taak 5 af: `quality.py` met 5 checks (rijen > 0, stop_id uniek, sleutels niet NULL, elke rit ≥ 2 stops, vertraging in [−5, 600] met max 0,1% uitschieters) + NULL-% per kolom. `load` stopt met `DataQualityError` als een check faalt. Oktober: alles OK (197 uitschieters = 0,011%). 15 tests groen. Volgende stap: taak 6 (12 maanden downloaden, laadtijd + DB-grootte in README).
- 2026-11-20 — Project verhuisd van `~/Documents` (iCloud-sync) naar `~/Projects/VoorspellerOV`. Reden: iCloud zette de verborgen-vlag op het `.pth`-bestand in `.venv`, en Python 3.14 slaat verborgen `.pth`-bestanden over → `ModuleNotFoundError`. Daarnaast zou iCloud de data uploaden. `.venv` opnieuw gemaakt.
- 2026-11-20 — Taak 6 af: 2025-11 t/m 2026-10 gedownload (383 MB, ~13 s) en geladen: 22.521.886 stops, 2.397.288 ritten, laadtijd ~11 s (piekgeheugen 1,6 GB), DB 498 MB. Alle checks OK (1.354 uitschieters = 0,007%). 4,3% vertrekken ≥ 5 min; nov 2025 het slechtst (6,0%). Cijfers in README. Volgende stap: taak 7 (stationsbestand → `raw.stations`).
- 2026-11-20 — Taak 7 af: `python -m ov_vertraging.download --stations` haalt `stations-2023-09.csv` (nieuwste; variant mét buitenland, 591 stations), `load` vult `raw.stations` (code, UIC, namen, land, type, lat/lng). 4 nieuwe checks (rijen, code uniek, coördinaten in Europa, ≥ 99% stops koppelt — nu 99,80%). Niet-gekoppeld: metrohaltes bij vervangend vervoer en buitenlandse stations, geen NL-treinstations. 24 tests groen. Volgende stap: taak 8 (GitHub-repo + MIT-licentie).
- 2026-11-20 — Taak 8 af: MIT-licentie toegevoegd, publieke repo https://github.com/jadendeleij/ov-vertraging aangemaakt en `main` gepusht (alleen code, geen data). Commit-e-mail voor dit project is het GitHub-noreply-adres (`git config user.email`, alleen lokaal), oude commits herschreven vóór de eerste push. **Fase 1 klaar** (wacht op jouw eigen controle). Volgende stap: Fase 2 op branch `fase-2-pipeline`, beginnend met de keuze voor de KNMI-bron.
- 2026-11-20 — Fase 2 gestart op branch `fase-2-pipeline`. Weerbron gekozen (met jou): KNMI-uurgegevensdienst (daggegevens.knmi.nl, geen API-key) i.p.v. KNMI Data Platform; afweging in README. `download_weather.py` haalt per maand alle stations op (herhaalt een maand tot die ≥ 3 dagen na maandeinde is opgehaald), `load_weather.py` vult `raw.weather_hourly` (bron-eenheden, uur 1–24 UTC) en `raw.weather_stations`; `load` laadt nu alles. 5 weerchecks. 12 maanden: 402.960 uurrijen = 46 stations × 8.760 uur, compleet. 41 tests groen. Bevindingen:
  - KNMI levert bij `end=...24` de volgende dag mee → loader filtert per bestand op eigen maand.
  - DuckDB-sniffer faalt op de lange commentaarkop → kopregels zelf tellen en `skip` gebruiken.
  - `WW` (weercode) is 100% leeg → uit het verzoek gehaald.
  - Niet elk station meet alles (26% geen temperatuur) → in `int_station_weather_map` per variabele het dichtstbijzijnde station kiezen dat die variabele meet.
  - DB-bestand stabiliseert rond ~920 MB (vrije blokken na `CREATE OR REPLACE` worden hergebruikt, niet teruggegeven).
  - Volgende stap: dbt-project in `transform/` (dbt-core + dbt-duckdb toevoegen aan pyproject), `sources.yml` en `stg_*`-modellen.
