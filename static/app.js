// Steam Market Intelligence - front-end logic (talks to server.py through /api/...)

const $ = (selector) => document.querySelector(selector);
const PLOT_CONFIG = { displayModeBar: false, responsive: true };
const fmt = (n) => Math.round(n).toLocaleString();
const money = (n) => "$" + Number(n).toFixed(2);

const state = { tiers: [], allTiers: [], minRecs: 0, tab: "market", benchLoaded: false };

// ---------- small helpers ----------
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

function showError(message) {
  const box = $("#error");
  box.textContent = "⚠ " + message;
  box.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => (box.hidden = true), 6000);
}

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

// numbers count up from 0 for a bit of life
function countUp(element, target, format = fmt, duration = 900) {
  const start = performance.now();
  function step(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    element.textContent = format(target * eased);
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function draw(id, figure) {
  const el = document.getElementById(id);
  figure.layout.autosize = true;
  Plotly.react(el, figure.data, figure.layout, PLOT_CONFIG);
}

function queryString() {
  return `tiers=${encodeURIComponent(state.tiers.join(","))}&min_recs=${state.minRecs}`;
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});

function switchTab(name) {
  state.tab = name;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  $("#filters").hidden = !(name === "market" || name === "data");

  if (name === "market") loadOverview();
  if (name === "data") loadData();
  if (name === "benchmark" && !state.benchLoaded) loadBenchmark("ELDEN RING");
}

// ---------- filters ----------
function buildFilters(tiers) {
  state.allTiers = tiers;
  state.tiers = [...tiers];
  const holder = $("#tier-chips");
  holder.innerHTML = "";
  tiers.forEach((tier) => {
    const chip = document.createElement("button");
    chip.className = "chip on";
    chip.textContent = tier;
    chip.addEventListener("click", () => {
      chip.classList.toggle("on");
      state.tiers = [...holder.querySelectorAll(".chip.on")].map((c) => c.textContent);
      filtersChanged();
    });
    holder.appendChild(chip);
  });

  $("#min-recs").addEventListener("input", (event) => {
    state.minRecs = Number(event.target.value);
    $("#min-recs-val").textContent = fmt(state.minRecs);
    filtersChanged();
  });
}

function filtersChanged() {
  clearTimeout(filtersChanged.timer);
  filtersChanged.timer = setTimeout(() => {
    if (state.tab === "market") loadOverview();
    if (state.tab === "data") loadData();
  }, 250);
}

// ---------- market overview ----------
async function loadOverview() {
  const charts = $("#market-charts");
  charts.classList.add("loading");
  try {
    const data = await api("/api/overview?" + queryString());
    const m = data.metrics;
    countUp($("#m-games"), m.games);
    countUp($("#m-price"), m.avg_price, money);
    countUp($("#m-recs"), m.avg_recs);
    countUp($("#m-rated"), m.pct_rated, (n) => n.toFixed(1) + "%");

    const empty = !data.figures;
    $("#market-empty").hidden = !empty;
    charts.hidden = empty;
    if (!empty) {
      draw("ch-tier", data.figures.tier);
      draw("ch-rate", data.figures.rate);
      draw("ch-genre", data.figures.genre);
      draw("ch-year", data.figures.year);
    }
  } catch (err) {
    showError(err.message);
  } finally {
    charts.classList.remove("loading");
  }
}

// ---------- benchmark ----------
const gameInput = $("#game-input");
const suggestBox = $("#suggest");

gameInput.addEventListener("input", () => {
  clearTimeout(gameInput.timer);
  gameInput.timer = setTimeout(async () => {
    const text = gameInput.value.trim();
    if (text.length < 2) { suggestBox.hidden = true; return; }
    try {
      const names = await api("/api/search?q=" + encodeURIComponent(text));
      suggestBox.innerHTML = "";
      names.forEach((name) => {
        const option = document.createElement("button");
        option.textContent = name;
        option.addEventListener("click", () => {
          gameInput.value = name;
          suggestBox.hidden = true;
          loadBenchmark(name);
        });
        suggestBox.appendChild(option);
      });
      suggestBox.hidden = names.length === 0;
    } catch (err) {
      showError(err.message);
    }
  }, 200);
});

gameInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") { suggestBox.hidden = true; loadBenchmark(gameInput.value.trim()); }
});
document.addEventListener("click", (event) => { if (!event.target.closest(".search")) suggestBox.hidden = true; });

function delta(element, diff, formatter) {
  element.textContent = (diff >= 0 ? "▲ " : "▼ ") + formatter(Math.abs(diff)) + " vs genre average";
  element.className = diff >= 0 ? "up" : "down";
}

async function loadBenchmark(name) {
  if (!name) return;
  try {
    const d = await api("/api/benchmark?name=" + encodeURIComponent(name));
    state.benchLoaded = true;
    gameInput.value = d.game.name;
    $("#bench-result").hidden = false;

    $("#b-name").textContent = d.game.name;
    $("#b-sub").textContent = `${d.game.developer} · published by ${d.game.publisher}`;
    $("#b-rank").textContent = `#${fmt(d.genre_stats.rank)} of ${fmt(d.genre_stats.peers)} in ${d.game.genre}`;

    $("#b-price").textContent = d.game.price === 0 ? "Free" : money(d.game.price);
    delta($("#b-price-d"), d.game.price - d.genre_stats.avg_price, money);
    countUp($("#b-recs"), d.game.recs);
    delta($("#b-recs-d"), d.game.recs - d.genre_stats.avg_recs, fmt);
    $("#b-genre").textContent = d.game.genre;
    $("#b-peers").textContent = fmt(d.genre_stats.peers) + " titles in genre";
    $("#b-year").textContent = d.game.year;

    draw("ch-radar", d.radar);

    const rows = d.rivals.map((r) =>
      `<tr><td>${escapeHtml(r.title)}</td><td class="num">${money(r.price)}</td><td class="num">${fmt(r.recs)}</td></tr>`
    ).join("");
    $("#rivals").innerHTML = rows
      ? `<table><tr><th>Title</th><th class="num">Price</th><th class="num">Recs</th></tr>${rows}</table>`
      : `<p class="hint">No direct price competitors found in this range.</p>`;
  } catch (err) {
    showError(err.message);
  }
}

// ---------- chat ----------
const messages = $("#messages");
const SUGGESTIONS = [
  "Top 10 RPGs under $20",
  "Free action games",
  "Top 5 strategy games released in 2024",
  "Indie games over 1000 recommendations",
  "Most popular racing games",
];

function addMessage(kind, html, extraClass = "") {
  const div = document.createElement("div");
  div.className = `msg ${kind} ${extraClass}`;
  div.innerHTML = html;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

function initChat() {
  addMessage("bot", "Hi! 👋 Ask me about any of the 65,000+ Steam games in plain English. Try one of the ideas below.");
  const holder = $("#suggestions");
  SUGGESTIONS.forEach((text) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = text;
    chip.addEventListener("click", () => ask(text));
    holder.appendChild(chip);
  });
}

async function ask(question) {
  if (!question.trim()) return;
  $("#chat-input").value = "";
  addMessage("user", escapeHtml(question));
  const typing = addMessage("bot", '<span class="typing"><span></span><span></span><span></span></span>');

  try {
    const d = await api("/api/chat?q=" + encodeURIComponent(question));
    typing.remove();
    const tags = d.understood.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("");
    const note = d.understood.length ? "" : "<br><small>I didn't spot any filters, so here are the most popular games overall.</small>";

    if (d.rows.length === 0) {
      addMessage("bot", `I couldn't find any games matching that. Try a different genre or price range!<div class="tags">${tags}</div>`);
      return;
    }

    const body = d.rows.map((r) =>
      `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.genres)}</td><td class="num">${r.price_numeric === 0 ? "Free" : money(r.price_numeric)}</td><td class="num">${fmt(r.recommendations)}</td><td class="num">${r.release_year}</td></tr>`
    ).join("");
    const card = addMessage("bot",
      `Found <b>${d.rows.length}</b> games.${note}<div class="tags">${tags}</div>
       <div id="chat-chart-${Date.now()}"></div>
       <div class="table-wrap"><table><tr><th>Title</th><th>Genres</th><th class="num">Price</th><th class="num">Recs</th><th class="num">Year</th></tr>${body}</table></div>`,
      "wide");
    Plotly.newPlot(card.querySelector("[id^=chat-chart]"), d.figure.data, d.figure.layout, PLOT_CONFIG);
    messages.scrollTop = messages.scrollHeight;
  } catch (err) {
    typing.remove();
    addMessage("bot", "😕 " + escapeHtml(err.message));
  }
}

$("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  ask($("#chat-input").value);
});

// ---------- raw data ----------
async function loadData() {
  $("#download").href = "/download.csv?" + queryString();
  try {
    const d = await api("/api/data?" + queryString());
    $("#data-count").textContent = `(top ${d.rows.length} by recommendations — download for everything)`;
    const body = d.rows.map((r) =>
      `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.genres)}</td><td>${escapeHtml(r.price_tier)}</td><td class="num">${money(r.price_numeric)}</td><td class="num">${fmt(r.recommendations)}</td><td class="num">${r.release_year}</td></tr>`
    ).join("");
    $("#data-table").innerHTML =
      `<tr><th>Title</th><th>Genres</th><th>Tier</th><th class="num">Price</th><th class="num">Recs</th><th class="num">Year</th></tr>${body}`;
  } catch (err) {
    showError(err.message);
  }
}

// ---------- start ----------
async function start() {
  initChat();
  try {
    const meta = await api("/api/meta");
    buildFilters(meta.tiers);
    document.querySelectorAll("[data-count]").forEach((el) => {
      countUp(el, meta[el.dataset.count], fmt, 1400);
    });
    loadOverview();
  } catch (err) {
    showError(err.message);
  }
}
start();
