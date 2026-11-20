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
python -m ov_vertraging.download_weather --start 2025-11 --end 2026-10
python -m ov_vertraging.load
cd transform && dbt build && dbt docs generate
```

Windows (PowerShell): gelijk, maar activeer met `.venv\Scripts\Activate.ps1` en gebruik `python` i.p.v. `python3`.

`dbt docs serve` (in `transform/`) opent de documentatie met de lineage-grafiek in je browser.

## Transformaties (dbt)

Het dbt-project staat in [`transform/`](transform/) en schrijft naar dezelfde DuckDB-database, in de schema's `staging`, `intermediate` en `marts`, naast `raw`.

**Staging** (views, één-op-één met de bron, opgeschoond):

| Model | Wat het doet |
|---|---|
| `stg_services` | Tijden naar Nederlandse tijd én UTC-uur, afgeleide velden `departure_hour` en `departure_weekday`, onmogelijke vertragingen (> 600 min) naar NULL met vlag, `is_rail_service` (vervangende bussen en metro eruit te filteren). `service_max_delay_min` bewust weggelaten (leakage). |
| `stg_stations` | Treinstations met nette namen, land en coördinaten. |
| `stg_weather` | KNMI-codes naar °C, mm, m/s, Beaufort en meters zicht; begin van het uurvak in UTC en lokale tijd. |
| `stg_weather_stations` | Weerstations met coördinaten en welke variabelen ze echt meten (34 van 51 meten temperatuur). |

**Intermediate** (koppelingen):

| Model | Wat het doet |
|---|---|
| `int_station_weather_map` | Elk treinstation → per variabele (temperatuur, neerslag, wind, zicht) het dichtstbijzijnde KNMI-station dat die variabele echt meet, via de haversine-afstand. Gemiddeld 14 km, maximaal 36 km voor Nederlandse stations; > 50 km = geen weer. |
| `int_departures_with_weather` | (view) 19,6 mln treinvertrekken met volgend station (`route_code`, 2.979 trajecten) en het weer van dat uur; 97,5 % heeft een temperatuur. Volgorde van stops op geplande tijd, niet op `stop_id`: bij omleidingen krijgt een extra stop een later id. Een view en geen tabel, zodat de 19,6 mln rijen niet twee keer op schijf staan. |

**Marts** (tabellen voor dashboard en model):

| Model | Wat het doet |
|---|---|
| `fct_departures` | Eén rij per treinvertrek met target `is_delayed_5min`, tijd, traject en weer (+ vlaggen `is_frost`, `is_wind_7bft`, `is_heavy_rain`). |
| `dim_station` | 645 stations: het stationsbestand plus 54 stations uit het treinarchief die er niet in staan (vooral buitenland), met gekoppelde weerstations. |
| `dim_date` | 366 dagen met weekdag, seizoen, feestdagen en schoolvakanties per regio (noord/midden/zuid). |
| `agg_route_hour` | Punctualiteit per traject × uur × weekdag (205.621 rijen): % ≥ 5 min, % geannuleerd, gemiddelde en p90-vertraging. |

**Target `is_delayed_5min`**: `true` = vertrokken met ≥ 5 min vertraging, `false` = < 5 min, `NULL` = geannuleerd of vertraging onbekend. Het model voorspelt dus *"als de trein rijdt, wat is de kans op ≥ 5 min vertraging?"*; annuleringen (6,0 % van de vertrekken) zijn een apart cijfer, zoals NS dat ook rapporteert. Van de 18,45 mln vertrekken met een target is 4,64 % vertraagd.

**Seeds**: feestdagen (berekend, Pasen via het Meeus-algoritme) en schoolvakanties (open data Rijksoverheid) staan als kleine CSV's in [`transform/seeds/`](transform/seeds/), gemaakt met `python -m ov_vertraging.calendar_seeds --years 2025 2026`. Zo draait `dbt build` zonder internet.

**Waarom koppelen op UTC?** Bij de overgang naar wintertijd (25 oktober 2026) komt lokaal 02:00 twee keer voor. Een koppeling op Nederlandse tijd zou dan elk vertrek aan twee weeruren hangen. In UTC is elk uur uniek. Een eigen dbt-test ([`assert_weather_dst_switch`](transform/tests/assert_weather_dst_switch.sql)) bewaakt dit.

**Tests**: 77 in totaal (`dbt build` ~20 s): `unique`/`not_null` op sleutels, `accepted_values` op treintype en regio, `relationships` tussen `fct_departures` en de dimensies, en eigen tests: [`value_between`](transform/tests/generic/value_between.sql) (bv. vertraging tussen −5 en 600 min), [`unique_combination`](transform/tests/generic/unique_combination.sql) (station × uur), twee tijdzonetests, een weerdekkingstest (≥ 95 % van de NL-vertrekken heeft weer), een test op de exacte targetdefinitie en een steekproef van bekende dagen in `dim_date`.

## Data

Treinarchief van Rijden de Treinen, november 2025 t/m oktober 2026 (12 maanden):

| | |
|---|---|
| Ruwe bestanden | 12 × ~33 MB gzip-CSV = 383 MB |
| Rijen in `raw.services` | 22.521.886 stops van 2.397.288 ritten |
| Downloadtijd | ~13 s |
| Laadtijd in DuckDB (incl. kwaliteitschecks) | ~11 s, piekgeheugen ~1,6 GB |
| Grootte `data/ov.duckdb` | 498 MB na eerste keer laden; stabiel ~920 MB bij herladen (zie hieronder) |
| Vertrekken ≥ 5 min vertraagd | 4,3 % (hoogst in nov 2025: 6,0 %, laagst in jul 2026: 3,4 %) |
| Stations in `raw.stations` | 591 (397 NL, 194 buitenland), met coördinaten en stationstype |
| Weer in `raw.weather_hourly` | 402.960 uurrijen = 46 weerstations × 8.760 uur, geen enkel uur ontbrekend (42 MB ruw, ~15 s download) |

Gemeten op een Mac met Apple Silicon. DuckDB leest de gzip-bestanden rechtstreeks; er gaan geen ruwe rijen door pandas.

Over de bestandsgrootte: `CREATE OR REPLACE` maakt de oude tabel vrij, maar DuckDB geeft die ruimte niet terug aan het besturingssysteem. Bij de volgende run worden die vrije blokken hergebruikt, dus het bestand stabiliseert rond 2× de data en groeit niet onbeperkt.

### Weerbron: waarom KNMI-uurgegevens

We gebruiken de [KNMI-uurgegevensdienst](https://www.daggegevens.knmi.nl/klimatologie/uurgegevens): gevalideerde uurwaarden van alle automatische weerstations.

| | KNMI-uurgegevens (gekozen) | KNMI Data Platform (hourly validated) |
|---|---|---|
| API-key | niet nodig | nodig |
| Formaat | CSV, één request per maand voor alle stations | NetCDF-bestanden |
| Stationscoördinaten | in de kop van het bestand | in de NetCDF-bestanden |
| Actualiteit | ~2 dagen vertraging | ~1–2 dagen vertraging |

De meetwaarden zijn dezelfde. De uurgegevensdienst is eenvoudiger en vereist geen secrets, wat ook CI makkelijker maakt. Risico: het is een oudere dienst. Als KNMI die uitzet, hoeft alleen [`download_weather.py`](src/ov_vertraging/download_weather.py) om naar het Data Platform.

Variabelen per uur (in `raw` in bron-eenheden, omrekenen gebeurt in dbt staging): temperatuur, neerslag, gemiddelde wind, windstoten, windrichting, zicht, luchtvochtigheid, bewolking, zonneschijn, en vlaggen voor mist, sneeuw, onweer en ijsvorming.

Valkuilen in deze bron, afgevangen in code en tests:
- Uren lopen van 1 t/m 24 in **UTC**: uur 5 = 04:00–05:00 UTC. Treintijden zijn Nederlandse tijd, dus de koppeling moet omrekenen (zomer-/wintertijd!).
- Veel waarden zijn in **tienden** (0,1 °C, 0,1 mm, 0,1 m/s); neerslag `-1` betekent < 0,05 mm (droog).
- Met een einde op uur 24 levert KNMI ook de **volgende dag** mee; de loader filtert per bestand op de eigen maand.
- Niet elk station meet alles: ~26 % van de uren heeft geen temperatuur (zee- en windstations). De koppeling trein → weerstation moet per variabele een station kiezen dat die variabele meet.
- De weercode (`WW`) levert deze dienst voor geen enkel station; die is daarom niet opgenomen.

Het stationsbestand (`stations-2023-09.csv`) is de nieuwste versie van Rijden de Treinen. We gebruiken de variant mét buitenlandse stations, omdat het treinarchief ook ritten naar Duitsland en België bevat. 99,8 % van alle stops koppelt aan een station; de rest zijn metrohaltes bij vervangend vervoer en enkele buitenlandse stations.

### Datakwaliteit

Bij elke laadronde draait [`quality.py`](src/ov_vertraging/quality.py). Faalt er één check, dan stopt de pipeline.
- `raw.services`: rijen > 0, `stop_id` uniek, sleutelkolommen niet NULL, elke rit ≥ 2 stops, vertraging binnen [−5, 600] min (max 0,1 % uitschieters).
- `raw.stations`: rijen > 0, `station_code` uniek, coördinaten binnen Europa, ≥ 99 % van de stops heeft een bekend station.
- `raw.weather_*`: rijen > 0, (station, datum, uur) uniek, uur tussen 1 en 24, temperatuur en windstoten plausibel, elk weerstation heeft coördinaten.

Bekende eigenaardigheden van de bron:
- Een handvol DB-treinen (Duitsland) heeft precies ~1440 min vertraging: een datumfout van 24 uur. Dat is 0,007 % van de rijen; ze worden in de staging-laag weggefilterd.
- Te vroeg vertrekken wordt als 0 min opgeslagen (geen negatieve vertragingen).
- `Service:Maximum delay` staat alleen op de eerste stop van een rit ingevuld.
- De data bevat ook vervangende bussen (`... ipv trein`) en internationale treinen.

## Bronnen

- Treindata: [Rijden de Treinen – treinarchief](https://www.rijdendetreinen.nl/open-data/treinarchief), licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Stations: [Rijden de Treinen – stations](https://www.rijdendetreinen.nl/open-data/stations), licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Weer: [KNMI – uurgegevens van het weer in Nederland](https://www.knmi.nl/nederland-nu/klimatologie/uurgegevens), bron: Koninklijk Nederlands Meteorologisch Instituut (KNMI).

## Licentie

De code valt onder de [MIT-licentie](LICENSE). De data valt onder de licenties van de bronnen hierboven.
