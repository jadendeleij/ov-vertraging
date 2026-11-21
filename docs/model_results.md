# Modelresultaten (automatisch gegenereerd door `python -m ov_vertraging.model.train`)

## Testset (sep–okt 2026)

| Model | PR-AUC ↑ | ROC-AUC ↑ | Brier ↓ | Gem. voorspeld | Werkelijk |
|---|---|---|---|---|---|
| baseline | 0.092 | 0.683 | 0.0391 | 4.1 % | 4.2 % |
| logistische regressie | 0.100 | 0.678 | 0.0390 | 4.0 % | 4.2 % |
| lightgbm | 0.107 | 0.701 | 0.0389 | 3.5 % | 4.2 % |

## LightGBM per treintype

| Treintype | Vertrekken | % vertraagd | PR-AUC | ROC-AUC |
|---|---|---|---|---|
| Sprinter | 1.508.483 | 3.7 % | 0.075 | 0.665 |
| Stoptrein | 643.854 | 2.8 % | 0.139 | 0.732 |
| Intercity | 638.699 | 6.2 % | 0.116 | 0.666 |
| Sneltrein | 68.537 | 6.3 % | 0.154 | 0.749 |
| Intercity direct | 32.848 | 8.8 % | 0.149 | 0.650 |

## LightGBM per maand

| Maand | Vertrekken | % vertraagd | PR-AUC | ROC-AUC |
|---|---|---|---|---|
| 10 | 1.479.110 | 4.6 % | 0.118 | 0.703 |
| 9 | 1.413.311 | 3.7 % | 0.098 | 0.702 |

## Dagen waarop LightGBM het meest onderschatte

| Datum | Vertrekken | Werkelijk | Voorspeld |
|---|---|---|---|
| 2026-10-23 | 48.462 | 8.8 % | 3.1 % |
| 2026-10-24 | 43.208 | 7.1 % | 3.1 % |
| 2026-10-22 | 49.843 | 6.9 % | 3.2 % |
| 2026-09-10 | 50.051 | 6.6 % | 3.6 % |
| 2026-10-25 | 37.865 | 5.7 % | 2.8 % |

## Belangrijkste features (gemiddelde |SHAP|)

| Feature | Gem. \|SHAP\| |
|---|---|
| `hist_route_hour_pct` | 0.282 |
| `route_code` | 0.152 |
| `hist_train_pct` | 0.137 |
| `planned_network_hour` | 0.111 |
| `departure_hour` | 0.110 |
| `stop_sequence` | 0.094 |
| `planned_station_hour` | 0.087 |
| `station_code` | 0.061 |
| `temperature_c` | 0.059 |
| `service_type` | 0.040 |
| `next_station_code` | 0.038 |
| `hist_train_n` | 0.038 |
