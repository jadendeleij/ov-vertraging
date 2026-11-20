# OV-vertragingsvoorspeller

Voorspelt de kans dat een trein in Nederland ≥ 5 minuten vertraging heeft bij vertrek, op basis van historische treindata en het weer.

> 🚧 In aanbouw — zie de roadmap hieronder.

## Roadmap

- [ ] Fase 1 – Data binnenhalen (treinarchief → DuckDB)
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
python -m ov_vertraging.download --start 2026-10 --end 2026-10
python -m ov_vertraging.load
```

Windows (PowerShell): gelijk, maar activeer met `.venv\Scripts\Activate.ps1` en gebruik `python` i.p.v. `python3`.

## Bronnen

- Treindata: [Rijden de Treinen – treinarchief](https://www.rijdendetreinen.nl/open-data/treinarchief), licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
