"""Steam Market Intelligence Platform - the website's Python backend.

Only the standard library + pandas + plotly + sqlite3 are used.
Start it with:  python server.py   then open http://localhost:8000
"""
import json
import mimetypes
import os
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import plotly.graph_objects as go

from db_pipeline import DB_PATH, PRICE_LABELS, SteamPipeline

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TIER_COLORS = ["#00f5d4", "#00bbf9", "#9b5de5", "#c04fd8", "#f15bb5", "#ff9f1c"]


# ---------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------
def records(df):
    """DataFrame -> list of dicts (NaN becomes null so the browser can read it)."""
    return json.loads(df.to_json(orient="records"))


def style(fig, height=330):
    """Give every chart the same dark, transparent look."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Space Grotesk, sans-serif", color="#cfd8ff"),
        margin=dict(l=10, r=10, t=10, b=10),
        height=height,
        showlegend=False,
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.07)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.07)", zeroline=False)
    return json.loads(fig.to_json())


def to_int(text, name, default):
    """Read a whole number from the URL, with a friendly error."""
    if text in (None, ""):
        return default
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{name} must be a whole number")


# ---------------------------------------------------------------
# Class 1: plain-English -> SQL
# ---------------------------------------------------------------
class QueryParser:
    """Turns a sentence like 'Top 5 RPGs under $20' into a SQL query."""

    GENRES = {
        "action": "Action",
        "rpg": "RPG",
        "strategy": "Strategy",
        "indie": "Indie",
        "adventure": "Adventure",
        "simulation": "Simulation",
        "casual": "Casual",
        "racing": "Racing",
        "sports": "Sports",
        "early access": "Early Access",
        "mmo": "Massively Multiplayer",
        "massively multiplayer": "Massively Multiplayer",
    }

    def parse(self, prompt):
        text = prompt.lower().strip()
        if not text:
            raise ValueError("Please type a question first.")

        conditions, params, understood = [], [], []

        # Find the first genre mentioned, then stop looking (break)
        genre = None
        for word, name in self.GENRES.items():
            if re.search(rf"\b{re.escape(word)}s?\b", text):
                genre = name
                break
        if genre:
            conditions.append("f.appid IN (SELECT appid FROM dim_genres WHERE genre = ?)")
            params.append(genre)
            understood.append(f"genre = {genre}")

        # Minimum recommendations, e.g. "over 500 recommendations"
        recs = re.search(r"(?:over|above|more than|at least|min|>)?\s*(\d[\d,]*)\s*\+?\s*(?:recs|recommendations|reviews)", text)
        if recs:
            amount = int(recs.group(1).replace(",", ""))
            conditions.append("f.recommendations >= ?")
            params.append(amount)
            understood.append(f"at least {amount:,} recommendations")
            text = text.replace(recs.group(0), " ")  # so the number isn't read as a price

        # Price: free / under X / over X
        under = re.search(r"(?:under|below|less than|cheaper than|<)\s*\$?\s*(\d+(?:\.\d+)?)", text)
        over = re.search(r"(?:over|above|more than|greater than|>)\s*\$?\s*(\d+(?:\.\d+)?)", text)
        if re.search(r"\bfree\b", text):
            conditions.append("f.price_numeric = 0")
            understood.append("free")
        elif under:
            conditions.append("f.price_numeric <= ?")
            params.append(float(under.group(1)))
            understood.append(f"price up to ${float(under.group(1)):g}")
            text = text.replace(under.group(0), " ")
        elif over:
            conditions.append("f.price_numeric >= ?")
            params.append(float(over.group(1)))
            understood.append(f"price from ${float(over.group(1)):g}")
            text = text.replace(over.group(0), " ")

        # Release year, e.g. "released in 2023"
        year = re.search(r"\b(20\d{2})\b", text)
        if year:
            conditions.append("f.release_year = ?")
            params.append(int(year.group(1)))
            understood.append(f"released in {year.group(1)}")

        # "top 5" -> how many rows (default 10, never more than 50)
        top = re.search(r"top\s*(\d+)", text)
        limit = min(max(int(top.group(1)), 1), 50) if top else 10

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            "SELECT f.name, f.genres, f.price_numeric, f.recommendations, f.release_year "
            f"FROM fact_steam_games f{where} ORDER BY f.recommendations DESC LIMIT ?"
        )
        params.append(limit)
        return sql, params, understood


# ---------------------------------------------------------------
# Class 2: everything that talks to the database
# ---------------------------------------------------------------
class SteamDatabase:
    def __init__(self, path):
        self.path = path
        self.parser = QueryParser()

    def query(self, sql, params=()):
        try:
            with closing(sqlite3.connect(self.path)) as conn:
                return pd.read_sql_query(sql, conn, params=params)
        except (sqlite3.Error, pd.errors.DatabaseError) as err:
            raise RuntimeError(f"Database problem: {err}")

    def _filter(self, tiers, min_recs):
        marks = ",".join("?" * len(tiers))
        return f"f.price_tier IN ({marks}) AND f.recommendations >= ?", list(tiers) + [min_recs]

    def _check_tiers(self, tiers):
        for tier in tiers:
            if tier not in PRICE_LABELS:
                raise ValueError(f"Unknown price tier: {tier}")

    # ---- Market overview ----
    def overview(self, tiers, min_recs):
        self._check_tiers(tiers)
        if not tiers:
            return {"metrics": {"games": 0, "avg_price": 0, "avg_recs": 0, "pct_rated": 0}, "figures": None}

        where, params = self._filter(tiers, min_recs)
        df = self.query(
            f"SELECT f.price_tier, f.price_numeric, f.recommendations, f.release_year "
            f"FROM fact_steam_games f WHERE {where}",
            params,
        )
        if df.empty:
            return {"metrics": {"games": 0, "avg_price": 0, "avg_recs": 0, "pct_rated": 0}, "figures": None}

        metrics = {
            "games": len(df),
            "avg_price": float(df["price_numeric"].mean()),
            "avg_recs": float(df["recommendations"].mean()),
            "pct_rated": float((df["recommendations"] > 0).mean() * 100),
        }

        # Chart 1 + 2: price tiers
        by_tier = df.groupby("price_tier").agg(
            games=("recommendations", "size"),
            avg_recs=("recommendations", "mean"),
            pct_rated=("recommendations", lambda s: (s > 0).mean() * 100),
        )
        by_tier = by_tier.reindex(PRICE_LABELS).dropna()
        colors = [TIER_COLORS[PRICE_LABELS.index(t)] for t in by_tier.index]

        fig_avg = go.Figure(go.Bar(
            x=list(by_tier.index), y=by_tier["avg_recs"].round(1).tolist(),
            marker_color=colors, customdata=by_tier["games"].tolist(),
            hovertemplate="%{x}<br>Avg recs: %{y:,.0f}<br>%{customdata:,} games<extra></extra>",
        ))
        fig_rate = go.Figure(go.Bar(
            x=list(by_tier.index), y=by_tier["pct_rated"].round(1).tolist(),
            marker_color=colors,
            hovertemplate="%{x}<br>%{y:.1f}% of games got recommendations<extra></extra>",
        ))
        fig_rate.update_yaxes(ticksuffix="%")

        # Chart 3: genres (one genre per row thanks to dim_genres)
        genre_df = self.query(
            "SELECT g.genre, COUNT(*) AS games, AVG(f.recommendations) AS avg_recs "
            "FROM fact_steam_games f JOIN dim_genres g ON g.appid = f.appid "
            f"WHERE {where} AND g.genre != 'Uncategorized' "
            "GROUP BY g.genre HAVING COUNT(*) >= 5 ORDER BY avg_recs DESC LIMIT 10",
            params,
        ).iloc[::-1]
        fig_genre = go.Figure(go.Bar(
            x=genre_df["avg_recs"].round(1).tolist(), y=genre_df["genre"].tolist(), orientation="h",
            marker=dict(color=genre_df["avg_recs"].tolist(), colorscale=[[0, "#9b5de5"], [1, "#00f5d4"]]),
            customdata=genre_df["games"].tolist(),
            hovertemplate="%{y}<br>Avg recs: %{x:,.0f}<br>%{customdata:,} games<extra></extra>",
        ))

        # Chart 4: how crowded is the market each year?
        by_year = df.groupby("release_year").size()
        fig_year = go.Figure(go.Scatter(
            x=by_year.index.tolist(), y=by_year.tolist(), mode="lines+markers",
            line=dict(color="#f15bb5", width=3), marker=dict(size=9, color="#00f5d4"),
            fill="tozeroy", fillcolor="rgba(241,91,181,0.18)",
            hovertemplate="%{x}: %{y:,} games released<extra></extra>",
        ))
        fig_year.update_xaxes(dtick=1)

        return {
            "metrics": metrics,
            "figures": {
                "tier": style(fig_avg),
                "rate": style(fig_rate),
                "genre": style(fig_genre),
                "year": style(fig_year),
            },
        }

    # ---- Game search box ----
    def search_games(self, text):
        if len(text) < 2:
            return []
        df = self.query(
            "SELECT name FROM fact_steam_games WHERE name LIKE ? ORDER BY recommendations DESC LIMIT 8",
            (f"%{text}%",),
        )
        return df["name"].tolist()

    # ---- Competitor benchmark ----
    def benchmark(self, name):
        found = self.query(
            "SELECT * FROM fact_steam_games WHERE name = ? ORDER BY recommendations DESC LIMIT 1", (name,)
        )
        if found.empty:
            raise ValueError(f"No game called '{name}' was found.")
        game = found.iloc[0]
        genre = game["primary_genre"]

        peers = self.query(
            "SELECT f.price_numeric, f.recommendations, f.release_year "
            "FROM fact_steam_games f JOIN dim_genres g ON g.appid = f.appid WHERE g.genre = ?",
            (genre,),
        )
        price, recs, year = float(game["price_numeric"]), int(game["recommendations"]), int(game["release_year"])

        # Percentiles: where does this game sit compared with its genre?
        scores = [
            float((peers["price_numeric"] <= price).mean() * 100),
            float((peers["recommendations"] <= recs).mean() * 100),
            float((peers["release_year"] <= year).mean() * 100),
        ]
        axes = ["Price", "Recommendations", "Newness"]
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=[50, 50, 50, 50], theta=axes + [axes[0]], fill="toself", name="Genre median",
            line=dict(color="#00bbf9", dash="dot"), fillcolor="rgba(0,187,249,0.10)",
        ))
        fig.add_trace(go.Scatterpolar(
            r=scores + [scores[0]], theta=axes + [axes[0]], fill="toself", name=game["name"],
            line=dict(color="#00f5d4", width=3), fillcolor="rgba(0,245,212,0.25)",
        ))
        fig.update_layout(
            polar=dict(bgcolor="rgba(0,0,0,0)",
                       radialaxis=dict(range=[0, 100], ticksuffix="%", gridcolor="rgba(255,255,255,0.12)"),
                       angularaxis=dict(gridcolor="rgba(255,255,255,0.12)")),
            showlegend=True, legend=dict(orientation="h", y=-0.1),
        )
        radar = style(fig, height=360)
        radar["layout"]["showlegend"] = True

        low, high = price * 0.7, max(price * 1.3, price + 2.0)
        rivals = self.query(
            "SELECT f.name AS title, f.price_numeric AS price, f.recommendations AS recs "
            "FROM fact_steam_games f JOIN dim_genres g ON g.appid = f.appid "
            "WHERE g.genre = ? AND f.name != ? AND f.price_numeric BETWEEN ? AND ? "
            "ORDER BY f.recommendations DESC LIMIT 5",
            (genre, game["name"], low, high),
        )

        return {
            "game": {
                "name": game["name"], "genre": genre, "price": price, "recs": recs, "year": year,
                "developer": game["developer"], "publisher": game["publisher"],
            },
            "genre_stats": {
                "avg_price": float(peers["price_numeric"].mean()),
                "avg_recs": float(peers["recommendations"].mean()),
                "peers": len(peers),
                "rank": int((peers["recommendations"] > recs).sum() + 1),
            },
            "radar": radar,
            "rivals": records(rivals),
        }

    # ---- Chat ----
    def chat(self, prompt):
        sql, params, understood = self.parser.parse(prompt)
        df = self.query(sql, params)
        if df.empty:
            return {"understood": understood, "rows": [], "figure": None}

        small = df.iloc[::-1]  # biggest bar on top
        fig = go.Figure(go.Bar(
            x=small["recommendations"].tolist(), y=small["name"].tolist(), orientation="h",
            marker=dict(color=small["price_numeric"].tolist(),
                        colorscale=[[0, "#00f5d4"], [1, "#f15bb5"]],
                        colorbar=dict(title="Price $", thickness=10)),
            hovertemplate="%{y}<br>%{x:,} recommendations<extra></extra>",
        ))
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
        height = 120 + 34 * len(small)
        return {"understood": understood, "rows": records(df), "figure": style(fig, height=height)}

    # ---- Raw data table + CSV download ----
    def table(self, tiers, min_recs, limit=None):
        self._check_tiers(tiers)
        if not tiers:
            return pd.DataFrame()
        where, params = self._filter(tiers, min_recs)
        sql = (
            "SELECT f.appid, f.name, f.genres, f.price_numeric, f.price_tier, f.recommendations, "
            f"f.release_year, f.developer FROM fact_steam_games f WHERE {where} "
            "ORDER BY f.recommendations DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.query(sql, params)


db = SteamDatabase(DB_PATH)


# ---------------------------------------------------------------
# Class 3: the web server
# ---------------------------------------------------------------
class SteamHandler(BaseHTTPRequestHandler):
    """Answers each browser request: either a page/file or a JSON api call."""

    def do_GET(self):
        url = urlparse(self.path)
        args = {key: values[0] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
        try:
            if url.path == "/api/meta":
                counts = db.query(
                    "SELECT COUNT(*) AS games, COUNT(DISTINCT release_year) AS years, "
                    "(SELECT COUNT(DISTINCT genre) FROM dim_genres WHERE genre != 'Uncategorized') AS genres "
                    "FROM fact_steam_games"
                )
                self.send_json({"tiers": PRICE_LABELS, **records(counts)[0]})
            elif url.path == "/api/overview":
                self.send_json(db.overview(self.tiers(args), to_int(args.get("min_recs"), "min_recs", 0)))
            elif url.path == "/api/search":
                self.send_json(db.search_games(args.get("q", "").strip()))
            elif url.path == "/api/benchmark":
                self.send_json(db.benchmark(args.get("name", "").strip()))
            elif url.path == "/api/chat":
                self.send_json(db.chat(args.get("q", "")))
            elif url.path == "/api/data":
                df = db.table(self.tiers(args), to_int(args.get("min_recs"), "min_recs", 0), limit=200)
                self.send_json({"rows": records(df)})
            elif url.path == "/download.csv":
                df = db.table(self.tiers(args), to_int(args.get("min_recs"), "min_recs", 0))
                self.send_bytes(df.to_csv(index=False).encode(), "text/csv", download="steam_market_data.csv")
            else:
                self.send_file(url.path)
        except ValueError as err:  # bad input from the browser
            self.send_json({"error": str(err)}, status=400)
        except RuntimeError as err:  # database trouble
            print("Server error:", err)
            self.send_json({"error": str(err)}, status=500)
        except Exception as err:  # anything unexpected - never crash the server
            print("Unexpected error:", repr(err))
            self.send_json({"error": "Something went wrong on the server."}, status=500)

    def tiers(self, args):
        text = args.get("tiers")
        if text is None:
            return list(PRICE_LABELS)
        return [t for t in text.split(",") if t]

    def send_bytes(self, body, content_type, status=200, download=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data).encode(), "application/json", status)

    def send_file(self, url_path):
        name = "index.html" if url_path in ("/", "") else unquote(url_path).lstrip("/")
        if name.startswith("static/"):
            name = name[len("static/"):]
        full = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self.send_json({"error": "Page not found"}, status=404)
            return
        with open(full, "rb") as handle:
            body = handle.read()
        content_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self.send_bytes(body, content_type)

    def log_message(self, format, *args):
        pass  # keep the terminal quiet


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found - building it from the CSV first...")
        SteamPipeline().run()

    port = int(os.environ.get("PORT", 8000))  # hosting sites tell us the port through PORT
    server = ThreadingHTTPServer(("0.0.0.0", port), SteamHandler)
    print(f"Steam Market Intelligence is running at http://localhost:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
