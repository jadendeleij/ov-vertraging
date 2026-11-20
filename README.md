# OV-vertragingsvoorspeller

Voorspelt de kans dat een trein in Nederland ≥ 5 minuten vertraging heeft bij vertrek, op basis van historische treindata en het weer.

> 🚧 In aanbouw — zie de roadmap hieronder.

## Roadmap

- [x] Fase 1 – Data binnenhalen (treinarchief + stations → DuckDB, kwaliteitschecks)
- [ ] Fase 2 – Datapipeline (KNMI-weer, dbt, Dagster)
- [ ] Fase 3 – Analyse & dashboard
- [ ] Fase 4 – Model (LightGBM)
- [ ] Fase 5 – Live API + web-app
- [ ] Fase 6 – Presenteren

## Zelf draaien

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m ov_vertraging.download --start 2025-11 --end 2026-10 --stations
python -m ov_vertraging.load
```

Windows (PowerShell): gelijk, maar activeer met `.venv\Scripts\Activate.ps1` en gebruik `python` i.p.v. `python3`.

## Data

Treinarchief van Rijden de Treinen, november 2025 t/m oktober 2026 (12 maanden):

| | |
|---|---|
| Ruwe bestanden | 12 × ~33 MB gzip-CSV = 383 MB |
| Rijen in `raw.services` | 22.521.886 stops van 2.397.288 ritten |
| Downloadtijd | ~13 s |
| Laadtijd in DuckDB (incl. kwaliteitschecks) | ~11 s, piekgeheugen ~1,6 GB |
| Grootte `data/ov.duckdb` | 498 MB |
| Vertrekken ≥ 5 min vertraagd | 4,3 % (hoogst in nov 2025: 6,0 %, laagst in jul 2026: 3,4 %) |
| Stations in `raw.stations` | 591 (397 NL, 194 buitenland), met coördinaten en stationstype |

Gemeten op een Mac met Apple Silicon. DuckDB leest de gzip-bestanden rechtstreeks; er gaan geen ruwe rijen door pandas.

Het stationsbestand (`stations-2023-09.csv`) is de nieuwste versie van Rijden de Treinen. We gebruiken de variant mét buitenlandse stations, omdat het treinarchief ook ritten naar Duitsland en België bevat. 99,8 % van alle stops koppelt aan een station; de rest zijn metrohaltes bij vervangend vervoer en enkele buitenlandse stations.

### Datakwaliteit

Bij elke laadronde draait [`quality.py`](src/ov_vertraging/quality.py). Faalt er één check, dan stopt de pipeline.
- `raw.services`: rijen > 0, `stop_id` uniek, sleutelkolommen niet NULL, elke rit ≥ 2 stops, vertraging binnen [−5, 600] min (max 0,1 % uitschieters).
- `raw.stations`: rijen > 0, `station_code` uniek, coördinaten binnen Europa, ≥ 99 % van de stops heeft een bekend station.

Bekende eigenaardigheden van de bron:
- Een handvol DB-treinen (Duitsland) heeft precies ~1440 min vertraging: een datumfout van 24 uur. Dat is 0,007 % van de rijen; ze worden in de staging-laag weggefilterd.
- Te vroeg vertrekken wordt als 0 min opgeslagen (geen negatieve vertragingen).
- `Service:Maximum delay` staat alleen op de eerste stop van een rit ingevuld.
- De data bevat ook vervangende bussen (`... ipv trein`) en internationale treinen.

## Bronnen

- Treindata: [Rijden de Treinen – treinarchief](https://www.rijdendetreinen.nl/open-data/treinarchief), licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Stations: [Rijden de Treinen – stations](https://www.rijdendetreinen.nl/open-data/stations), licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Licentie

De code valt onder de [MIT-licentie](LICENSE). De data valt onder de licenties van de bronnen hierboven.
