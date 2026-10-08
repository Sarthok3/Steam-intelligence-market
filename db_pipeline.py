"""Data pipeline: CSV -> clean -> SQLite warehouse.

Run it once with:  python db_pipeline.py
(server.py also runs it automatically if the database is missing.)
"""
import os
import sqlite3

import pandas as pd

CSV_PATH = os.path.join("data", "steam_games.csv")
DB_PATH = os.path.join("data", "steam_warehouse.db")

PRICE_BINS = [-1, 0, 4.99, 9.99, 19.99, 29.99, float("inf")]
PRICE_LABELS = [
    "Free",
    "$0.01-$4.99",
    "$5.00-$9.99",
    "$10.00-$19.99",
    "$20.00-$29.99",
    "$30.00+",
]


def clean_price(value):
    """Turn a price like '$9.99', 'Free' or 9.99 into a float."""
    if pd.isna(value):
        return 0.0
    text = str(value).lower().strip()
    if "free" in text:
        return 0.0
    try:
        return float(text.replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


class SteamPipeline:
    """Extract -> Transform -> Load, one method per step."""

    def __init__(self, csv_path=CSV_PATH, db_path=DB_PATH):
        self.csv_path = csv_path
        self.db_path = db_path
        self.df = None

    def extract(self):
        print("1. Reading CSV...")
        try:
            self.df = pd.read_csv(self.csv_path)
        except FileNotFoundError:
            raise SystemExit(f"Error: {self.csv_path} not found. Put the CSV in the data/ folder.")
        self.df.columns = self.df.columns.str.strip().str.lower()

    def transform(self):
        print("2. Cleaning data...")
        df = self.df.drop_duplicates(subset="appid").copy()
        df["price_numeric"] = df["price"].apply(clean_price)
        df["price_tier"] = pd.cut(df["price_numeric"], bins=PRICE_BINS, labels=PRICE_LABELS).astype(str)
        df["genres"] = df["genres"].fillna("Uncategorized")
        df["developer"] = df["developer"].fillna("Unknown")
        df["publisher"] = df["publisher"].fillna("Unknown")
        df["recommendations"] = df["recommendations"].fillna(0).astype(int)
        df["primary_genre"] = df["genres"].str.split(";").str[0]
        self.df = df

    def load(self):
        print("3. Loading into SQLite...")
        # one row per (game, genre) so genres can be counted properly
        genres = self.df[["appid", "genres"]].copy()
        genres["genre"] = genres["genres"].str.split(";")
        genres = genres.explode("genre")[["appid", "genre"]]

        conn = sqlite3.connect(self.db_path)
        try:
            self.df.to_sql("fact_steam_games", conn, if_exists="replace", index=False)
            genres.to_sql("dim_genres", conn, if_exists="replace", index=False)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_games_name ON fact_steam_games(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_games_appid ON fact_steam_games(appid)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_genres_genre ON dim_genres(genre)")
            conn.execute("DROP VIEW IF EXISTS view_genre_performance")
            conn.execute(
                """
                CREATE VIEW view_genre_performance AS
                SELECT g.genre,
                       COUNT(*) AS total_games,
                       AVG(f.recommendations) AS avg_recommendations,
                       AVG(f.price_numeric) AS avg_price
                FROM dim_genres g JOIN fact_steam_games f ON f.appid = g.appid
                GROUP BY g.genre
                """
            )
            conn.commit()
        finally:
            conn.close()

    def run(self):
        self.extract()
        self.transform()
        self.load()
        print(f"Done: {len(self.df):,} games saved to {self.db_path}")


if __name__ == "__main__":
    SteamPipeline().run()
