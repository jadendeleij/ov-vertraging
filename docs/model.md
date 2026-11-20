# Model: kans op ≥ 5 minuten vertraging

## 1. Probleemdefinitie

**Vraag:** *"Als deze trein rijdt, hoe groot is de kans dat hij met 5 minuten of meer vertraging vertrekt?"*

| | |
|---|---|
| **Eenheid** | Eén vertrek van een trein vanaf een station (`marts.fct_departures`). |
| **Target** | `is_delayed_5min` = vertrokken met ≥ 5 min vertraging. Geannuleerde vertrekken en onbekende vertragingen hebben geen target en doen niet mee (uitval is een apart cijfer, zoals NS dat rapporteert). |
| **Scope** | Binnenlandse treinen (Intercity, Intercity direct, Sprinter, Stoptrein, Sneltrein) vanaf een Nederlands station. Internationale treinen niet: hun vertraging ontstaat grotendeels buiten Nederland en buiten onze data. |
| **Voorspelmoment** | Een paar uur vóór vertrek. Het model mag dus alleen gebruiken wat dan bekend is: de dienstregeling, de kalender, de weersverwachting en de geschiedenis tot en met **gisteren**. |
| **Uitvoer** | Een kans tussen 0 en 1. Omdat maar ~4 % van de vertrekken vertraagd is, is een goed *gekalibreerde* kans nuttiger dan een ja/nee. |

## 2. Features

| Groep | Features | Bekend vooraf? |
|---|---|---|
| Rit | treintype, vervoerder, vertrekstation, volgend station, traject (`route_code`), positie in de rit (`stop_sequence`, `is_first_stop`) | ✅ dienstregeling (¹) |
| Tijd | uur, weekdag, maand, weekend | ✅ |
| Kalender | feestdag, aantal regio's met schoolvakantie (0–3) | ✅ |
| Drukte | aantal geplande vertrekken op dit station in dit uur, en landelijk in dit uur | ✅ dienstregeling |
| Weer | temperatuur, neerslag, gemiddelde wind, windstoten, zicht, luchtvochtigheid, sneeuw, mist, ijsvorming | ⚠️ in training de **gemeten** waarden; in productie een **verwachting** (²) |
| Geschiedenis | % vertraagd van dit traject × uur, van dit station, en van dit treinnummer — berekend over alle dagen **vóór** de vertrekdag | ✅ alleen verleden |

¹ `stop_sequence` telt ook stops mee die door een omleiding op de dag zelf zijn toegevoegd. Dat komt zelden voor (0,5 % van de ritten) en is als kleine onzuiverheid geaccepteerd.
² Een weersverwachting is minder precies dan een meting. De modelscores op de testset zijn daarom een **bovengrens** voor wat in productie haalbaar is.

## 3. Leakage voorkomen

*Leakage* = het model leert van informatie die het op het voorspelmoment niet kan weten, en lijkt daardoor beter dan het is.

**Verboden als feature** (afgedwongen door een test, [`tests/test_model_features.py`](../tests/test_model_features.py)):

- de vertraging zelf (`departure_delay_min`) en alles wat ervan is afgeleid;
- de vertraging van **dezelfde rit** op eerdere of latere stops (`arrival_delay_min`);
- `service_max_delay_min` en de annuleringsvelden van dezelfde rit (`is_cancelled`, `service_completely_cancelled`, `service_partly_cancelled`);
- perronwijzigingen (`platform_change`), datafout-vlaggen (`has_invalid_delay`);
- `agg_route_hour` uit de marts: die is over de **hele** periode berekend, dus ook over de toekomst. De historische features worden daarom opnieuw berekend met een venster dat stopt op de dag vóór vertrek (ook getest).

## 4. Train / validatie / test: split op tijd

| Set | Periode | Vertrekken | % vertraagd |
|---|---|---|---|
| Train | nov 2025 – jul 2026 | 12,9 mln | 4,1 % |
| Validatie | aug 2026 | 1,45 mln | 3,6 % |
| Test | sep – okt 2026 | 2,9 mln | 4,2 % |

**Nooit random splitten.** Bij een random split zit in de training een vertrek van 17:00 en in de test dezelfde trein van 17:00 op diezelfde stormachtige dag — het model "kent" dan de toekomst al. Met een split op tijd voorspellen we echt maanden die het model nooit heeft gezien, net als in productie.

**Geheugen:** de laptop heeft 8 GB werkgeheugen. Trainen gebeurt daarom op een willekeurige **steekproef binnen de trainingsperiode** (dat is geen leakage: alle rijen komen nog steeds uit het verleden); de evaluatie gebeurt op **alle** vertrekken van de testperiode.

## 5. Modellen

1. **Baseline** — het historische % vertraagd per traject × uur uit de trainingsperiode (met *smoothing*: weinig waarnemingen → dichter bij het landelijke gemiddelde). Elk slimmer model moet hier overheen.
2. **Logistische regressie** — lineair, goed uitlegbaar; categorische features one-hot, numerieke gestandaardiseerd.
3. **LightGBM** — gradient boosted trees met native categorische features; kleine hyperparameter-zoektocht op de validatieset, daarna opnieuw getraind op train + validatie.

## 6. Evaluatie (op de testset)

- **PR-AUC** (average precision) — de hoofdmaat: bij een zeldzame uitkomst (4 %) zegt die meer dan ROC-AUC.
- **ROC-AUC** — hoe goed rangschikt het model vertraagde boven niet-vertraagde vertrekken.
- **Brier-score** — gemiddelde kwadratische fout van de kans (lager is beter); samen met de **kalibratiecurve** laat dit zien of "10 % kans" ook echt 10 % betekent.
- Uitsplitsing per **treintype** en per **maand**, **SHAP**-samenvatting (welke features wegen het zwaarst) en een **foutanalyse**.

*Resultaten volgen in sectie 7 na de training.*
