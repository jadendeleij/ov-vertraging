/* OV-vertragingsvoorspeller — website zonder framework.
 *
 * Vier pagina's via de hash in de URL (#/, #/vertrekbord, #/inzichten, #/over), zodat
 * links deelbaar zijn en de browserknoppen werken. Alle data komt van dezelfde FastAPI
 * die deze bestanden serveert: /stations, /predict, /departures/{station}, /insights.
 */
"use strict";

const TZ = "Europe/Amsterdam";
const TIMEOUT_MS = 90_000; // gratis hosting: de eerste aanvraag na een pauze duurt ~1 minuut
const REFRESH_MS = 60_000;
const $app = document.getElementById("app");

// ---------- hulpfuncties ----------

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const nf1 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
const pct = (p, digits = 1) =>
  p == null ? "–" : (digits ? nf1 : nf0).format(p * 100) + "%";
const timeFmt = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const hhmm = (iso) => (iso ? timeFmt.format(new Date(iso)) : "–");
const dayFmt = new Intl.DateTimeFormat("nl-NL", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
const monthFmt = new Intl.DateTimeFormat("nl-NL", { month: "short", year: "numeric" });
const capital = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
/** Factoruitleg met stationsnamen i.p.v. codes ('station BGN' -> 'station Bergen op Zoom'). */
const factorText = (s) =>
  capital(String(s).replace(/\b(station|traject) ([A-Z]{2,6})(?:-([A-Z]{2,6}))?\b/g, (m, w, a, b) =>
    `${w} ${stationName(a)}${b ? " → " + stationName(b) : ""}`));

const RISK = {
  laag: "Laag risico",
  gemiddeld: "Gemiddeld risico",
  hoog: "Verhoogd risico",
};
const riskPill = (level) =>
  level ? `<span class="pill risk-${esc(level)}">${esc(RISK[level] || level)}</span>` : "";

/** Huidige tijd als 'YYYY-MM-DDTHH:MM' in Nederlandse tijd (voor datetime-local). */
function localInput(date = new Date(), roundTo = 5) {
  const ms = Math.ceil(date.getTime() / (roundTo * 60_000)) * roundTo * 60_000;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(ms)).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}
const isoToInput = (iso) => localInput(new Date(iso), 1);

function bft(ms) {
  if (ms == null) return null;
  const limits = [0.3, 1.6, 3.4, 5.5, 8, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7];
  const i = limits.findIndex((l) => ms < l);
  return i === -1 ? 12 : i;
}

/** fetch met time-out en een nette foutmelding uit FastAPI's 'detail'. */
async function api(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      ...options,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof body.detail === "string" ? body.detail : "Er ging iets mis bij het ophalen van de gegevens.";
      throw Object.assign(new Error(detail), { status: res.status });
    }
    return body;
  } catch (err) {
    if (err.name === "AbortError") throw Object.assign(new Error("De server reageert niet. Probeer het over een minuut opnieuw."), { status: 0 });
    if (err.status === undefined) throw Object.assign(new Error("Geen verbinding met de server."), { status: 0 });
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* geen opslag: niet erg */ } },
};

/** Toont na een paar seconden een uitleg dat de gratis server opstart. */
function coldStartHint(el) {
  const t = setTimeout(() => {
    if (el.isConnected) el.insertAdjacentHTML("beforeend", `<p class="muted" style="font-size:13px;margin-top:6px">De server wordt gewekt (gratis hosting). De eerste aanvraag kan tot een minuut duren.</p>`);
  }, 6000);
  return () => clearTimeout(t);
}

// ---------- stations ----------

let stations = [];
const stationsReady = api("/stations")
  .then((list) => {
    stations = list;
    document.getElementById("stations").innerHTML = list
      .map((s) => `<option value="${esc(s.name || s.code)}">${esc(s.code)}</option>`)
      .join("");
  })
  .catch(() => {});
const stationName = (code) => stations.find((s) => s.code === code)?.name || code;

api("/health")
  .then((h) => {
    document.getElementById("model-version").textContent = `model ${h.model_version} · data t/m ${h.history_until}`;
  })
  .catch(() => {});

// ---------- router ----------

let cleanup = () => {};
const routes = { voorspel: renderPredict, vertrekbord: renderBoard, inzichten: renderInsights, over: renderAbout };

function parseHash() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [name, ...rest] = path.split("/").filter(Boolean);
  return { name: routes[name] ? name : "voorspel", rest, params: new URLSearchParams(query) };
}

function router() {
  cleanup();
  cleanup = () => {};
  const route = parseHash();
  document.querySelectorAll("[data-route]").forEach((a) => {
    if (a.dataset.route === route.name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const titles = { voorspel: "Voorspel", vertrekbord: "Vertrekbord", inzichten: "Inzichten", over: "Over" };
  document.title = `${titles[route.name]} · OV-vertragingsvoorspeller`;
  const result = routes[route.name](route);
  if (typeof result === "function") cleanup = result;
}

/** Zet de query in de URL zonder de pagina opnieuw te tekenen. */
function setQuery(name, params, rest = []) {
  const path = [name === "voorspel" ? "" : name, ...rest].filter(Boolean).join("/");
  const q = params.toString();
  history.replaceState(null, "", `#/${path}${q ? "?" + q : ""}`);
}

window.addEventListener("hashchange", router);

// ---------- thema ----------

document.querySelector(".theme-toggle").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme
    ? root.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  store.set("thema", root.dataset.theme);
});

// =====================================================================
// Voorspel
// =====================================================================

const EXAMPLES = [
  ["Utrecht Centraal", "Amsterdam Centraal"],
  ["Leiden Centraal", "Den Haag Centraal"],
  ["Bergen op Zoom", "Rotterdam Blaak"],
];

function renderPredict({ params }) {
  const van = params.get("van") || "";
  const naar = params.get("naar") || "";
  const tijd = params.get("tijd") || localInput();

  $app.innerHTML = `
    <section class="hero">
      <h1>Komt mijn trein op tijd?</h1>
      <p>De kans dat je trein vijf minuten of later vertrekt, berekend uit een jaar treindata en de actuele KNMI-weersverwachting.</p>
    </section>

    <form class="search" id="search" autocomplete="off" novalidate>
      <div class="field">
        <label for="van">Van</label>
        <input id="van" name="van" list="stations" placeholder="Vertrekstation" value="${esc(van)}" required>
      </div>
      <button class="swap" type="button" id="swap" aria-label="Wissel vertrek- en aankomststation">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4v16M7 4 4 7M7 4l3 3M17 20V4m0 16-3-3m3 3 3-3"/></svg>
      </button>
      <div class="field">
        <label for="naar">Naar</label>
        <input id="naar" name="naar" list="stations" placeholder="Aankomststation" value="${esc(naar)}" required>
      </div>
      <div class="field">
        <label for="tijd">Vertrek</label>
        <input id="tijd" name="tijd" type="datetime-local" value="${esc(tijd)}" required>
        <div class="quick-when chips" role="group" aria-label="Snel een tijd kiezen">
          <button type="button" class="chip" data-when="0">Nu</button>
          <button type="button" class="chip" data-when="60">Over 1 uur</button>
          <button type="button" class="chip" data-when="morgen">Morgen 08:00</button>
        </div>
      </div>
      <button class="btn" type="submit" id="go">Voorspel</button>
    </form>

    <div id="out"></div>
  `;

  const form = document.getElementById("search");
  const $van = form.van, $naar = form.naar, $tijd = form.tijd;
  const out = document.getElementById("out");

  document.getElementById("swap").addEventListener("click", () => {
    [$van.value, $naar.value] = [$naar.value, $van.value];
  });
  form.querySelectorAll("[data-when]").forEach((b) =>
    b.addEventListener("click", () => {
      const w = b.dataset.when;
      if (w === "morgen") {
        const d = new Date(Date.now() + 86_400_000);
        $tijd.value = localInput(d).slice(0, 11) + "08:00";
      } else {
        $tijd.value = localInput(new Date(Date.now() + Number(w) * 60_000));
      }
    }),
  );

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    run();
  });

  function showEmpty() {
    out.innerHTML = `
      <div class="quick" role="group" aria-label="Voorbeeldreizen">
        <span class="muted" style="font-size:13.5px;align-self:center">Probeer:</span>
        ${EXAMPLES.map(([a, b]) => `<button type="button" class="chip" data-a="${esc(a)}" data-b="${esc(b)}">${esc(a)} → ${esc(b)}</button>`).join("")}
      </div>
      <div class="empty">
        <div class="card"><h3>1 · Kies je reis</h3><p>Vertrek- en aankomststation en het tijdstip. Het model zoekt de directe trein in de dienstregeling.</p></div>
        <div class="card"><h3>2 · Het model rekent</h3><p>Op basis van historische stiptheid van het traject, drukte, kalender en de weersverwachting voor dat uur.</p></div>
        <div class="card"><h3>3 · Jij beslist</h3><p>Je ziet de kans, de belangrijkste redenen en welke treinen rond dat tijdstip betrouwbaarder zijn.</p></div>
      </div>`;
    out.querySelectorAll("[data-a]").forEach((b) =>
      b.addEventListener("click", () => {
        $van.value = b.dataset.a;
        $naar.value = b.dataset.b;
        run();
      }),
    );
  }

  function showLoading() {
    out.innerHTML = `
      <div class="result" aria-busy="true">
        <p class="loading-text" id="loading">Trein zoeken in de dienstregeling en weersverwachting ophalen…</p>
        <div class="result-main">
          <div class="card verdict">
            <div class="skel" style="height:14px;width:40%"></div>
            <div class="skel" style="height:64px;width:55%"></div>
            <div class="skel" style="height:26px;width:35%"></div>
            <div class="skel" style="height:18px;width:70%;margin-top:10px"></div>
          </div>
          <div class="card">
            <div class="skel" style="height:16px;width:30%"></div>
            <div class="skel" style="height:14px;width:90%;margin-top:18px"></div>
            <div class="skel" style="height:14px;width:80%;margin-top:12px"></div>
            <div class="skel" style="height:14px;width:85%;margin-top:12px"></div>
            <div class="skel" style="height:34px;width:100%;margin-top:18px"></div>
          </div>
        </div>
        <div class="card"><div class="skel" style="height:120px"></div></div>
      </div>`;
    return coldStartHint(document.getElementById("loading").parentElement);
  }

  function showError(err) {
    const m = /Bedoel je: (.*)\?$/.exec(err.message);
    const which = /Station '(.*)' niet gevonden/.exec(err.message)?.[1];
    const target = which && which === $naar.value ? $naar : $van;
    const suggestions = m ? m[1].split(", ") : [];
    out.innerHTML = `
      <div class="alert" role="alert">
        <div><strong>${err.status === 404 && which ? "Station niet gevonden." : "Geen voorspelling mogelijk."}</strong>
        ${esc(err.message.replace(/ Bedoel je: .*$/, ""))}</div>
        ${suggestions.length ? `<div class="chips"><span class="muted" style="align-self:center;font-size:13.5px">Bedoel je:</span>${suggestions.map((s) => `<button type="button" class="chip" data-s="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : ""}
      </div>`;
    out.querySelectorAll("[data-s]").forEach((b) =>
      b.addEventListener("click", () => {
        target.value = b.dataset.s;
        run();
      }),
    );
  }

  let busy = false;
  async function run() {
    if (busy) return;
    if (!$van.value.trim() || !$naar.value.trim()) {
      ($van.value.trim() ? $naar : $van).focus();
      return;
    }
    busy = true;
    const go = document.getElementById("go");
    go.disabled = true;
    setQuery("voorspel", new URLSearchParams({ van: $van.value.trim(), naar: $naar.value.trim(), tijd: $tijd.value }));
    const stop = showLoading();
    try {
      const r = await api("/predict", {
        method: "POST",
        body: JSON.stringify({ from_station: $van.value.trim(), to_station: $naar.value.trim(), departure_time: $tijd.value }),
      });
      showResult(r);
    } catch (err) {
      showError(err);
    } finally {
      stop();
      busy = false;
      go.disabled = false;
    }
  }

  function showResult(r) {
    const t = r.train;
    const dep = new Date(t.planned_departure);
    const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: TZ }).format(dep));
    const ratio = r.probability / r.average_probability;
    const compare =
      ratio >= 1.15 ? `${nf1.format(ratio)}× zo hoog als bij een gemiddeld vertrek`
      : ratio <= 0.87 ? `lager dan bij een gemiddeld vertrek (${pct(r.average_probability)})`
      : `ongeveer gelijk aan een gemiddeld vertrek (${pct(r.average_probability)})`;
    const better = r.alternatives
      .filter((a) => !a.chosen && a.probability < r.probability * 0.8)
      .sort((a, b) => a.probability - b.probability)[0];

    out.innerHTML = `
      <div class="result">
        <div class="result-main">
          <article class="card verdict">
            <div>
              <p class="verdict__label">Kans op ≥ 5 min vertraging bij vertrek</p>
              <p class="verdict__pct txt-${esc(r.risk_level)}" style="margin-top:8px">${nf1.format(r.probability * 100)}<small>%</small></p>
            </div>
            <div class="verdict__row">
              ${riskPill(r.risk_level)}
              <span class="context">${esc(compare)}</span>
            </div>
            ${better ? `<p class="context">Betrouwbaarder: de trein van <b>${hhmm(better.planned_departure)}</b> (${pct(better.probability)}).</p>` : ""}
            <div class="trainline">
              <span class="type">${esc(t.type || "Trein")}</span>
              <span>${t.number ? esc(t.number) : ""}</span>
              <span class="times">${hhmm(t.planned_departure)} → ${hhmm(t.planned_arrival)}</span>
            </div>
            <p class="context">${esc(capital(dayFmt.format(dep)))} · ${esc(r.from_station.name)} → ${esc(r.to_station.name)}${t.operator ? ` · ${esc(t.operator)}` : ""}</p>
          </article>

          <article class="card">
            <h2>Waarom deze kans?</h2>
            <p class="card__sub">De drie factoren met de grootste invloed op deze voorspelling.</p>
            <ul class="factors">
              ${r.factors.map((f) => `
                <li>
                  <span class="arrow ${f.effect === "verhoogt" ? "up" : "down"}" aria-hidden="true">${f.effect === "verhoogt" ? "↑" : "↓"}</span>
                  <span>${esc(factorText(f.description))} <span class="muted">— ${esc(f.effect)} de kans</span></span>
                </li>`).join("")}
            </ul>
            <p class="weather">${weatherLine(r.weather)}</p>
          </article>
        </div>

        ${r.alternatives.length > 1 ? `
        <article class="card">
          <div class="card__head">
            <div><h2>Andere vertrektijden</h2><p class="card__sub">Directe treinen rond hetzelfde tijdstip, met hun kans op vertraging.</p></div>
          </div>
          <div class="alts" role="list">
            ${r.alternatives.map((a) => `
              <button type="button" class="alt" role="listitem" data-t="${esc(a.planned_departure)}" ${a.chosen ? 'aria-current="true"' : ""}
                aria-label="${esc(hhmm(a.planned_departure))}, ${esc(pct(a.probability))} kans op vertraging">
                <span class="alt__time">${hhmm(a.planned_departure)}</span>
                <span class="alt__pct txt-${esc(a.risk_level)}">${pct(a.probability)}</span>
                <span class="alt__tag">${a.chosen ? "gekozen" : esc(a.type || "")}</span>
              </button>`).join("")}
          </div>
        </article>` : ""}

        <article class="card">
          <div class="card__head">
            <div>
              <h2>Betrouwbaarheid van dit traject per uur</h2>
              <p class="card__sub">${esc(r.from_station.name)} → ${esc(stationName(t.next_stop?.code) || t.next_stop?.name || "volgend station")}: aandeel vertrekken met ≥ 5 min vertraging, afgelopen 12 maanden.</p>
            </div>
          </div>
          ${hourBars(r.route_by_hour, hour)}
        </article>

        <p class="note">${esc(r.note)} Model ${esc(r.model_version)}.</p>
      </div>`;

    out.querySelectorAll(".alt[data-t]").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.getAttribute("aria-current") === "true") return;
        $tijd.value = isoToInput(b.dataset.t);
        run();
      }),
    );
  }

  if (van && naar) run();
  else showEmpty();
}

function weatherLine(w) {
  if (!w || w.temperature_c == null) return "Weersverwachting voor dit uur niet beschikbaar; het model rekent met gemiddeld weer.";
  const parts = [`${nf1.format(w.temperature_c)} °C`, `${nf1.format(w.precipitation_mm ?? 0)} mm neerslag`];
  if (w.wind_speed_ms != null) parts.push(`wind ${bft(w.wind_speed_ms)} Bft${w.wind_gust_ms != null ? `, stoten ${bft(w.wind_gust_ms)} Bft` : ""}`);
  if (w.visibility_m != null) parts.push(`zicht ${w.visibility_m >= 10000 ? "> 10 km" : nf1.format(w.visibility_m / 1000) + " km"}`);
  const extra = [w.has_snow && "sneeuw", w.has_fog && "mist", w.has_ice && "ijzel"].filter(Boolean);
  return `Weer (KNMI-verwachting): ${parts.join(" · ")}${extra.length ? " · " + extra.join(", ") : ""}`;
}

function hourBars(values, current) {
  if (!values?.length || values.every((v) => v == null)) {
    return `<p class="muted">Te weinig historische ritten op dit traject om per uur te tonen.</p>`;
  }
  const max = Math.max(...values.filter((v) => v != null), 0.01);
  return `
    <div class="bars" role="img" aria-label="Staafdiagram: vertraagd percentage per uur van de dag">
      ${values.map((v, h) => `
        <div class="bars__col">
          <span class="tip ${h < 4 ? "tip--left" : h > 19 ? "tip--right" : ""}">${String(h).padStart(2, "0")}:00 · ${v == null ? "geen ritten" : pct(v) + " vertraagd"}</span>
          <div class="bars__bar ${v == null ? "is-empty" : ""} ${h === current ? "is-now" : ""}" style="height:${v == null ? 0 : Math.max(3, (v / max) * 100)}%"></div>
        </div>`).join("")}
    </div>
    <div class="bars__axis" aria-hidden="true">${values.map((_, h) => `<span>${h % 3 === 0 ? String(h).padStart(2, "0") : ""}</span>`).join("")}</div>
    <div class="legend"><span><i style="background:var(--accent);border-radius:2px"></i>gekozen vertrekuur</span><span><i style="background:var(--line-strong);border-radius:2px"></i>overige uren</span></div>`;
}

// =====================================================================
// Vertrekbord
// =====================================================================

const TYPE_FILTERS = {
  alle: { label: "Alle", test: () => true },
  intercity: { label: "Intercity", test: (t) => /^intercity/i.test(t || "") },
  sprinter: { label: "Sprinter", test: (t) => /^(sprinter|stoptrein|sneltrein)/i.test(t || "") },
  internationaal: { label: "Internationaal", test: (t) => !/^(intercity|sprinter|stoptrein|sneltrein)/i.test(t || "") },
};

function renderBoard({ rest, params }) {
  const station = decodeURIComponent(rest[0] || "") || store.get("bord-station") || "Utrecht Centraal";
  let filter = TYPE_FILTERS[params.get("type")] ? params.get("type") : "alle";
  let data = null;
  let selected = null;
  let sheetOpen = false; // mobiel: detailpaneel als onderblad, blijft open bij verversen
  let timer = null;

  $app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Vertrekbord</h1>
        <p>Live vertrektijden van NS, met per trein de voorspelde kans op vijf minuten of meer vertraging.</p>
      </div>
    </div>
    <div class="board-tools">
      <form class="board-search" id="board-form" autocomplete="off">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true" style="color:var(--muted)"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>
        <label class="sr" for="board-station">Station</label>
        <input id="board-station" list="stations" value="${esc(station)}" placeholder="Zoek een station">
        <button class="btn" type="submit">Toon</button>
      </form>
      <div class="chips" role="group" aria-label="Filter op treinsoort">
        ${Object.entries(TYPE_FILTERS).map(([k, f]) => `<button type="button" class="chip" data-f="${k}" aria-pressed="${k === filter}">${f.label}</button>`).join("")}
      </div>
      <p class="board-meta" id="board-meta"></p>
    </div>
    <div id="board"></div>
  `;

  const $board = document.getElementById("board");
  const $meta = document.getElementById("board-meta");
  const $input = document.getElementById("board-station");

  document.getElementById("board-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const s = $input.value.trim();
    if (!s) return;
    location.hash = `#/vertrekbord/${encodeURIComponent(s)}${filter !== "alle" ? "?type=" + filter : ""}`;
  });
  $app.querySelectorAll("[data-f]").forEach((b) =>
    b.addEventListener("click", () => {
      filter = b.dataset.f;
      $app.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", x.dataset.f === filter));
      setQuery("vertrekbord", new URLSearchParams(filter !== "alle" ? { type: filter } : {}), [encodeURIComponent(station)]);
      draw();
    }),
  );

  function skeleton() {
    $board.innerHTML = `
      <div class="board"><div class="card deps" aria-busy="true">
        <div class="deps__head"><span>Vertrek</span><span>Bestemming</span><span>Soort</span><span>Spoor</span><span style="text-align:right">Kans</span></div>
        ${Array.from({ length: 8 }, () => `<div class="dep" style="cursor:default"><div class="skel" style="height:16px"></div><div class="skel" style="height:16px;width:70%"></div><div class="skel" style="height:14px"></div><div class="skel" style="height:14px"></div><div class="skel" style="height:22px"></div></div>`).join("")}
      </div></div>`;
    $meta.innerHTML = `<span class="loading-text">Vertrektijden ophalen bij NS…</span>`;
    return coldStartHint($meta.parentElement);
  }

  async function load(first = false) {
    const stop = first ? skeleton() : () => {};
    try {
      data = await api(`/departures/${encodeURIComponent(station)}`);
      store.set("bord-station", data.station.name || station);
      if (first) $input.value = data.station.name || station;
      $meta.innerHTML = `<span class="live-dot" aria-hidden="true"></span>Bijgewerkt ${timeFmt.format(new Date())} · ververst elke minuut`;
      draw();
    } catch (err) {
      if (first || !data) {
        const m = /Bedoel je: (.*)\?$/.exec(err.message);
        $meta.textContent = "";
        $board.innerHTML = `
          <div class="alert" role="alert" style="max-width:none;margin:0">
            <div><strong>${err.status === 503 ? "Live vertrektijden zijn nu niet beschikbaar." : err.status === 404 ? "Station niet gevonden." : "Vertrektijden ophalen mislukt."}</strong>
            ${esc(err.status === 503 ? "Deze omgeving heeft geen NS-sleutel ingesteld. Voorspellingen voor een reis werken wel." : err.message.replace(/ Bedoel je: .*$/, ""))}</div>
            ${m ? `<div class="chips"><span class="muted" style="align-self:center;font-size:13.5px">Bedoel je:</span>${m[1].split(", ").map((s) => `<a class="chip" href="#/vertrekbord/${encodeURIComponent(s)}">${esc(s)}</a>`).join("")}</div>` : ""}
          </div>`;
      } else {
        $meta.textContent = `Verversen mislukt om ${timeFmt.format(new Date())}; de lijst hieronder kan verouderd zijn.`;
      }
    } finally {
      stop();
    }
  }

  function draw() {
    if (!data) return;
    const deps = data.departures.filter((d) => TYPE_FILTERS[filter].test(d.type));
    if (selected && !deps.some((d) => key(d) === selected)) selected = null;
    const wide = matchMedia("(min-width: 901px)").matches;
    if (!selected && wide && deps.length) selected = key(deps.find((d) => !d.cancelled) || deps[0]);

    $board.innerHTML = `
      <div class="board">
        <div class="card deps">
          <div class="deps__head"><span>Vertrek</span><span>Bestemming</span><span>Soort</span><span style="text-align:center">Spoor</span><span style="text-align:right">Kans</span></div>
          ${deps.length ? deps.map(row).join("") : `<p class="detail-empty" style="display:block">Geen vertrekken van dit type in het komende uur.</p>`}
        </div>
        <aside class="card detail" id="detail" aria-live="polite"></aside>
      </div>`;
    $board.querySelectorAll(".dep[data-k]").forEach((b) =>
      b.addEventListener("click", () => {
        selected = b.dataset.k;
        sheetOpen = true;
        $board.querySelectorAll(".dep").forEach((x) => x.setAttribute("aria-current", x.dataset.k === selected));
        drawDetail(true);
      }),
    );
    drawDetail(false);
  }

  const key = (d) => `${d.train_number}-${d.planned_departure}`;

  function row(d) {
    const late = d.current_delay_min > 0 ? `<span class="dep__late">+${d.current_delay_min}</span>` : "";
    const prob = d.cancelled
      ? `<span class="pill pill--plain risk-hoog">Rijdt niet</span>`
      : d.probability == null
        ? `<span class="pill pill--plain risk-geen">–</span>`
        : `<span class="pill risk-${esc(d.risk_level)}">${pct(d.probability)}</span>`;
    return `
      <button type="button" class="dep ${d.cancelled ? "is-cancelled" : ""}" data-k="${esc(key(d))}" aria-current="${key(d) === selected}">
        <span class="dep__time"><span>${hhmm(d.planned_departure)}</span>${late}</span>
        <span style="min-width:0">
          <span class="dep__dest" style="display:block">${esc(d.direction || "Onbekend")}</span>
          <span class="dep__via" style="display:block">${d.next_stop?.name ? "Eerste stop: " + esc(d.next_stop.name) : ""}<span class="m-track">${d.planned_track ? ` · spoor ${esc(d.planned_track)}` : ""} · ${esc(d.type || "")}</span></span>
        </span>
        <span class="dep__type">${esc(d.type || "")}</span>
        <span class="dep__track">${d.planned_track ? `<span>${esc(d.planned_track)}</span>` : "–"}</span>
        <span class="dep__prob">${prob}</span>
      </button>`;
  }

  function drawDetail(open) {
    const $d = document.getElementById("detail");
    const d = data.departures.find((x) => key(x) === selected);
    if (!d) {
      $d.innerHTML = `<p class="detail-empty">Kies een trein voor de voorspelling en de uitleg.</p>`;
      $d.classList.remove("is-open");
      return;
    }
    const tijd = d.planned_departure ? isoToInput(d.planned_departure) : "";
    $d.innerHTML = `
      <button type="button" class="detail__close" aria-label="Sluiten">✕</button>
      <p class="muted" style="font-size:13.5px">${esc(d.type || "Trein")} ${esc(d.train_number || "")}${d.planned_track ? ` · spoor ${esc(d.planned_track)}` : ""}</p>
      <h2>→ ${esc(d.direction || "Onbekend")}</h2>
      ${d.cancelled
        ? `<p class="verdict__pct txt-hoog" style="font-size:30px">Rijdt niet</p><p class="muted">Deze trein is door NS geannuleerd.</p>`
        : d.probability == null
          ? `<p class="muted" style="margin-top:16px">Voor deze trein is geen voorspelling beschikbaar.</p>`
          : `<p class="verdict__pct txt-${esc(d.risk_level)}">${nf1.format(d.probability * 100)}<small>%</small></p>
             ${riskPill(d.risk_level)}
             <p class="muted" style="font-size:13px;margin-top:10px">Kans op ≥ 5 min vertraging bij vertrek, berekend vóór vertrek (zonder de live status).</p>
             <ul class="factors">${d.factors.map((f) => `
               <li><span class="arrow ${f.effect === "verhoogt" ? "up" : "down"}" aria-hidden="true">${f.effect === "verhoogt" ? "↑" : "↓"}</span>
               <span>${esc(factorText(f.description))}</span></li>`).join("")}</ul>`}
      <dl>
        <dt>Gepland</dt><dd>${hhmm(d.planned_departure)}</dd>
        <dt>Actueel</dt><dd>${d.actual_departure ? hhmm(d.actual_departure) + (d.current_delay_min > 0 ? ` <span class="txt-hoog">(+${d.current_delay_min} min)</span>` : " (op tijd)") : "–"}</dd>
        ${d.next_stop?.name ? `<dt>Eerste stop</dt><dd>${esc(d.next_stop.name)}</dd>` : ""}
        ${d.operator ? `<dt>Vervoerder</dt><dd>${esc(d.operator)}</dd>` : ""}
      </dl>
      ${d.direction && !d.cancelled ? `<a class="btn" href="#/?${new URLSearchParams({ van: data.station.name || data.station.code, naar: d.direction, tijd })}">Volledige voorspelling →</a>` : ""}`;
    $d.querySelector(".detail__close").addEventListener("click", () => {
      sheetOpen = false;
      $d.classList.remove("is-open");
    });
    if (open || sheetOpen) $d.classList.add("is-open");
  }

  const onResize = () => data && draw();
  window.addEventListener("resize", onResize);
  load(true);
  timer = setInterval(() => document.visibilityState === "visible" && load(false), REFRESH_MS);
  return () => {
    clearInterval(timer);
    window.removeEventListener("resize", onResize);
  };
}

// =====================================================================
// Inzichten
// =====================================================================

const WEEKDAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"];

function renderInsights({ params }) {
  let months = [3, 6, 12].includes(Number(params.get("periode"))) ? Number(params.get("periode")) : 12;
  let type = params.get("type") || "";

  $app.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Inzichten</h1>
        <p id="ins-sub">Stiptheid van binnenlandse treinen op basis van het treinarchief van Rijden de Treinen.</p>
      </div>
      <div class="filters">
        <div class="select"><label for="f-periode">Periode</label>
          <select id="f-periode">${[3, 6, 12].map((m) => `<option value="${m}" ${m === months ? "selected" : ""}>Laatste ${m} maanden</option>`).join("")}</select></div>
        <div class="select"><label for="f-type">Treinsoort</label>
          <select id="f-type"><option value="">Alle treinen</option></select></div>
      </div>
    </div>
    <div id="ins"></div>`;

  const $ins = document.getElementById("ins");
  const $type = document.getElementById("f-type");
  document.getElementById("f-periode").addEventListener("change", (e) => { months = Number(e.target.value); load(); });
  $type.addEventListener("change", (e) => { type = e.target.value; load(); });

  let seq = 0;
  async function load() {
    const my = ++seq;
    const q = new URLSearchParams({ periode: months, ...(type ? { type } : {}) });
    setQuery("inzichten", q);
    $ins.setAttribute("aria-busy", "true");
    if (!$ins.innerHTML) {
      $ins.innerHTML = `
        <div class="kpis">${[1, 2, 3].map(() => `<div class="card"><div class="skel" style="height:14px;width:50%"></div><div class="skel" style="height:36px;width:40%;margin-top:12px"></div></div>`).join("")}</div>
        <div class="grid-2"><div class="card"><div class="skel" style="height:360px"></div></div><div class="card"><div class="skel" style="height:360px"></div></div></div>`;
    } else {
      $ins.style.opacity = 0.55;
    }
    const stop = coldStartHint($ins);
    try {
      const d = await api(`/insights?months=${months}${type ? "&type=" + encodeURIComponent(type) : ""}`);
      if (my !== seq) return;
      if ($type.options.length === 1) {
        $type.insertAdjacentHTML("beforeend", d.service_types.map((t) => `<option ${t === type ? "selected" : ""}>${esc(t)}</option>`).join(""));
      }
      draw(d);
    } catch (err) {
      if (my !== seq) return;
      $ins.innerHTML = `<div class="alert" role="alert" style="max-width:none;margin:0"><div><strong>Inzichten laden mislukt.</strong> ${esc(err.message)}</div></div>`;
    } finally {
      stop();
      $ins.style.opacity = "";
      $ins.removeAttribute("aria-busy");
    }
  }

  function draw(d) {
    const from = monthFmt.format(new Date(d.period.from + "T12:00:00"));
    const to = monthFmt.format(new Date(d.period.to + "T12:00:00"));
    const k = d.kpis;
    document.getElementById("ins-sub").textContent =
      `${d.service_type || "Binnenlandse treinen"}, ${from} – ${to} · ${nf0.format(k?.departures || 0)} vertrekken. Bron: treinarchief van Rijden de Treinen.`;
    if (!k) {
      $ins.innerHTML = `<div class="card"><p class="muted">Geen gegevens voor deze selectie.</p></div>`;
      return;
    }
    const delta = (v, unit, goodWhenUp, fmt) => {
      if (v == null || !d.period.compared_to) return `<p class="kpi__delta">Geen vergelijkbare periode ervoor beschikbaar</p>`;
      const good = goodWhenUp ? v > 0 : v < 0;
      const cls = Math.abs(v) < 1e-9 ? "" : good ? "good" : "bad";
      const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
      return `<p class="kpi__delta"><b class="${cls}">${sign}${fmt(Math.abs(v))} ${unit}</b> t.o.v. de ${d.period.months} maanden ervoor</p>`;
    };
    const pp = (v) => nf1.format(v * 100);
    $ins.innerHTML = `
      <div class="kpis">
        <article class="card kpi">
          <p class="kpi__label">Punctualiteit</p>
          <p class="kpi__value">${pct(k.punctuality)}</p>
          ${delta(k.delta?.punctuality, "procentpunt", true, pp)}
        </article>
        <article class="card kpi">
          <p class="kpi__label">Gemiddelde vertraging per vertrek</p>
          <p class="kpi__value">${nf1.format(k.avg_delay_min)} min</p>
          ${delta(k.delta?.avg_delay_min, "min", false, (v) => nf1.format(v))}
        </article>
        <article class="card kpi">
          <p class="kpi__label">Uitgevallen vertrekken</p>
          <p class="kpi__value">${pct(k.cancelled)}</p>
          ${delta(k.delta?.cancelled, "procentpunt", false, pp)}
        </article>
      </div>

      <div class="grid-2">
        <article class="card">
          <div class="card__head"><div><h2>Stiptheid per station</h2><p class="card__sub">Aandeel vertrekken met minder dan 5 minuten vertraging.</p></div></div>
          ${stationMap(d.stations)}
        </article>
        <div class="col">
        <article class="card">
          <div class="card__head"><div><h2>Vertraging per uur en weekdag</h2><p class="card__sub">Aandeel vertrekken met ≥ 5 minuten vertraging, 05:00–23:00.</p></div></div>
          ${heatmap(d.heatmap)}
        </article>
        ${ranking(d.stations)}
        </div>
      </div>

      <article class="card">
        <div class="card__head"><div><h2>Invloed van het weer</h2><p class="card__sub">Aandeel vertrekken met ≥ 5 minuten vertraging per weersomstandigheid (KNMI-uurwaarden bij het dichtstbijzijnde weerstation). Omstandigheden kunnen samen voorkomen.</p></div></div>
        ${weatherBars(d.weather)}
      </article>`;
    bindMap();
  }

  load();
}

const riskClass = (delayed) => (delayed < 0.03 ? "laag" : delayed < 0.06 ? "gemiddeld" : "hoog");

function stationMap(list) {
  if (!list.length) return `<p class="muted">Geen stations met voldoende vertrekken in deze selectie.</p>`;
  const W = 400, H = 470;
  const [lng0, lng1, lat0, lat1] = [3.3, 7.25, 50.72, 53.55];
  const x = (lng) => ((lng - lng0) / (lng1 - lng0)) * W;
  const y = (lat) => H - ((lat - lat0) / (lat1 - lat0)) * H;
  const maxN = Math.max(...list.map((s) => s.departures));
  const colour = { laag: "var(--low)", gemiddeld: "var(--mid)", hoog: "var(--high)" };
  // Grote stations eerst tekenen, zodat kleine erbovenop zichtbaar blijven.
  const dots = [...list].sort((a, b) => b.departures - a.departures).map((s) => {
    const r = 2.2 + 7 * Math.sqrt(s.departures / maxN);
    const cls = riskClass(1 - s.punctuality);
    return `<circle cx="${x(s.lng).toFixed(1)}" cy="${y(s.lat).toFixed(1)}" r="${r.toFixed(1)}" fill="${colour[cls]}" fill-opacity="0.85"
      data-name="${esc(s.name)}" data-p="${s.punctuality}" data-n="${s.departures}" tabindex="0"><title>${esc(s.name)}: ${pct(s.punctuality)} op tijd</title></circle>`;
  }).join("");
  return `
    <svg class="map" viewBox="0 0 ${W} ${H}" role="img" aria-label="Kaart van Nederland met stations gekleurd naar stiptheid">${dots}</svg>
    <p class="map-tip" id="map-tip"><span class="muted">Wijs een station aan voor details.</span></p>
    <div class="legend">
      <span><i style="background:var(--low)"></i>≥ 97% op tijd</span>
      <span><i style="background:var(--mid)"></i>94–97%</span>
      <span><i style="background:var(--high)"></i>&lt; 94%</span>
      <span>grootte = aantal vertrekken</span>
    </div>`;
}

function ranking(list) {
  if (!list.length) return "";
  const worst = list.slice(0, 5);
  const big = [...list].sort((a, b) => b.departures - a.departures).slice(0, 25);
  const best = [...big].sort((a, b) => b.punctuality - a.punctuality).slice(0, 5);
  const bigWorst = [...big].sort((a, b) => a.punctuality - b.punctuality).slice(0, 5);
  const ol = (rows) => `<ol>${rows.map((s) => `<li>${esc(s.name)} <span>${pct(s.punctuality)}</span></li>`).join("")}</ol>`;
  return `
    <article class="card">
      <div class="card__head"><div><h2>Stations vergeleken</h2><p class="card__sub">Punctualiteit; grote stations = de 25 met de meeste vertrekken.</p></div></div>
      <div class="ranking">
        <div><h3>Stiptst (grote stations)</h3>${ol(best)}</div>
        <div><h3>Minst stipt (grote stations)</h3>${ol(bigWorst)}</div>
        <div style="grid-column:1/-1;margin-top:10px"><h3>Minst stipt (alle stations)</h3>${ol(worst)}</div>
      </div>
    </article>`;
}

function bindMap() {
  const tip = document.getElementById("map-tip");
  if (!tip) return;
  document.querySelectorAll(".map circle").forEach((c) => {
    const show = () => {
      tip.innerHTML = `<b>${esc(c.dataset.name)}</b> · ${pct(Number(c.dataset.p))} op tijd · ${nf0.format(Number(c.dataset.n))} vertrekken`;
    };
    c.addEventListener("mouseenter", show);
    c.addEventListener("focus", show);
    c.addEventListener("click", show);
  });
}

function heatmap(cells) {
  if (!cells.length) return `<p class="muted">Geen gegevens.</p>`;
  const values = cells.map((c) => c.delayed);
  const lo = Math.min(...values), hi = Math.max(...values);
  const level = (v) => (hi === lo ? 2 : Math.min(4, Math.floor(((v - lo) / (hi - lo)) * 5)));
  const byKey = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c.delayed]));
  const hours = Array.from({ length: 19 }, (_, i) => i + 5);
  const days = [1, 2, 3, 4, 5, 6, 7];
  return `
    <div style="overflow-x:auto">
    <table class="heat" aria-label="Vertraagd percentage per uur (kolommen) en weekdag (rijen)">
      <thead><tr><th></th>${hours.map((h) => `<th scope="col">${String(h).padStart(2, "0")}</th>`).join("")}</tr></thead>
      <tbody>${days.map((d) => `<tr><th scope="row">${WEEKDAYS[d - 1]}</th>${hours.map((h) => {
        const v = byKey.get(`${d}-${h}`);
        return v == null
          ? `<td style="background:var(--heat-0)"></td>`
          : `<td style="background:var(--heat-${level(v)})"><span class="tip">${WEEKDAYS[d - 1]} ${String(h).padStart(2, "0")}:00 · ${pct(v)}</span><span class="sr">${pct(v)}</span></td>`;
      }).join("")}</tr>`).join("")}</tbody>
    </table></div>
    <div class="legend">
      <span>${pct(lo)}</span>
      <span class="scale">${[0, 1, 2, 3, 4].map((i) => `<i style="background:var(--heat-${i})"></i>`).join("")}</span>
      <span>${pct(hi)} vertraagd</span>
    </div>`;
}

function weatherBars(rows) {
  if (!rows.length) return `<p class="muted">Geen gegevens.</p>`;
  const base = rows.find((r) => r.condition === "Gewoon weer");
  const max = Math.max(...rows.map((r) => r.delayed));
  return `<div class="wbars">${rows.map((r) => {
    const isBase = r === base;
    const diff = base && !isBase ? r.delayed - base.delayed : null;
    const fill = isBase ? "is-base" : "";
    const colour = !isBase && r.delayed >= (base?.delayed || 0) * 1.5 ? "background:var(--high)" : "";
    return `
      <div class="wbar">
        <span class="wbar__name">${esc(r.condition)}<small>${nf0.format(r.departures)} vertrekken${diff != null ? ` · ${diff >= 0 ? "+" : "−"}${nf1.format(Math.abs(diff) * 100)} pp` : ""}</small></span>
        <span class="wbar__track"><span class="wbar__fill ${fill}" style="display:block;width:${(r.delayed / max) * 100}%;${colour}"></span></span>
        <span class="wbar__val">${pct(r.delayed)}</span>
      </div>`;
  }).join("")}</div>`;
}

// =====================================================================
// Over
// =====================================================================

function renderAbout() {
  $app.innerHTML = `
    <section class="about-hero">
      <p class="eyebrow">Over dit project</p>
      <h1>Van ruwe treindata tot een live voorspelling</h1>
      <p>Een end-to-end data- en machine-learningproject: een jaar Nederlandse treinritten en KNMI-weer, verwerkt in een geteste datapipeline, vertaald naar een model dat per vertrek de kans op vertraging inschat, en ontsloten via deze website en een openbare API.</p>
      <div class="actions">
        <a class="btn" href="https://github.com/jadendeleij/ov-vertraging" rel="noopener">Bekijk de code op GitHub ↗</a>
        <a class="btn btn--ghost" href="#/">Probeer de voorspeller</a>
      </div>
    </section>

    <div class="stats">
      <div class="card"><p class="stat__value">22,5 mln</p><p class="stat__label">treinstops, nov 2025 – okt 2026</p></div>
      <div class="card"><p class="stat__value">402.960</p><p class="stat__label">uurwaarnemingen van 46 KNMI-stations</p></div>
      <div class="card"><p class="stat__value">2,5×</p><p class="stat__label">beter dan gokken in het vinden van vertragingen</p></div>
      <div class="card"><p class="stat__value">8 MB</p><p class="stat__label">live export, in plaats van een database van 3 GB</p></div>
    </div>

    <article class="card section">
      <div class="card__head"><div><h2>Architectuur</h2><p class="card__sub">Elke stap is idempotent en getest; Dagster draait de hele keten dagelijks.</p></div></div>
      <img class="arch" src="architectuur.svg" alt="Architectuur: Rijden de Treinen, KNMI en Rijksoverheid worden met Python in DuckDB geladen, met dbt getransformeerd tot marts, gebruikt voor analyse, model en monitoring, en via een slanke export live gezet met FastAPI op Render." loading="lazy">
      <div class="steps">
        <div class="step"><b>1 · Inladen</b><span>Downloads en laden in DuckDB met 14 kwaliteitschecks die de pipeline blokkeren bij fouten.</span></div>
        <div class="step"><b>2 · Transformeren</b><span>dbt in drie lagen (staging, intermediate, marts) met 77 tests; elk vertrek gekoppeld aan het weer van dat uur.</span></div>
        <div class="step"><b>3 · Modelleren</b><span>LightGBM met een split op tijd, een test tegen datalekken en uitleg per voorspelling via SHAP.</span></div>
        <div class="step"><b>4 · Live</b><span>FastAPI in Docker op Render, met de KNMI-weersverwachting en live NS-vertrektijden. CI op elke push.</span></div>
      </div>
    </article>

    <div class="grid-2 section">
      <article class="card">
        <div class="card__head"><div><h2>Modelresultaten</h2><p class="card__sub">Testset: september–oktober 2026, 2,9 mln vertrekken die het model nooit zag.</p></div></div>
        <div style="overflow-x:auto">
        <table class="table">
          <thead><tr><th>Model</th><th>PR-AUC ↑</th><th>ROC-AUC ↑</th><th>Brier ↓</th></tr></thead>
          <tbody>
            <tr><td>Willekeurig gokken</td><td>0,042</td><td>0,500</td><td>–</td></tr>
            <tr><td>Baseline: historisch % per traject × uur</td><td>0,092</td><td>0,683</td><td>0,0391</td></tr>
            <tr><td>Logistische regressie</td><td>0,100</td><td>0,678</td><td>0,0390</td></tr>
            <tr class="best"><td>LightGBM</td><td>0,107</td><td>0,701</td><td>0,0389</td></tr>
          </tbody>
        </table></div>
        <p class="muted" style="font-size:13px;margin-top:12px">De 10% vertrekken met de hoogste voorspelde kans bevatten 28% van alle werkelijke vertragingen.</p>
      </article>
      <article class="card">
        <div class="card__head"><div><h2>Wat het model wel en niet kan</h2></div></div>
        <div class="prose">
          <p><b>Wel:</b> structurele patronen herkennen — trajecten en uren die vaker vertraging hebben, drukte, de kalender en weersomstandigheden als sneeuw, vorst en zware windstoten.</p>
          <p><b>Niet:</b> incidenten voorspellen. Storingen, aanrijdingen en stakingen zijn vooraf onzichtbaar; ze verklaren de grootste fouten van het model.</p>
          <p><b>Bewaakt:</b> dagelijkse monitoring vergelijkt voorspelling en werkelijkheid. In oktober 2026 voorspelde het model ~28% te laag — het signaal om maandelijks opnieuw te trainen.</p>
        </div>
      </article>
    </div>

    <article class="card section">
      <h2>Techniek</h2>
      <div class="stack" style="margin-top:12px">
        ${["Python", "DuckDB", "dbt", "Dagster", "pandas", "LightGBM", "scikit-learn", "SHAP", "MLflow", "FastAPI", "Docker", "GitHub Actions", "Render", "pytest", "ruff", "HTML/CSS/JavaScript"].map((t) => `<span>${t}</span>`).join("")}
      </div>
      <p class="muted" style="font-size:13.5px;margin-top:16px">Deze website is bewust zonder framework of bouwstap gemaakt en wordt door dezelfde FastAPI-dienst geserveerd als de <a href="/docs">API</a>. Treindata: Rijden de Treinen (CC BY 4.0) · Weer: KNMI · Geen relatie met NS.</p>
    </article>`;
}

// ---------- start ----------

stationsReady.finally(() => {});
router();
