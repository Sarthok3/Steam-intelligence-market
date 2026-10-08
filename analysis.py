"""Quick static analysis of the Steam dataset (saves charts into data/).

Run with:  python analysis.py
"""
import os

import matplotlib
matplotlib.use("Agg")  # save charts as images, no pop-up window needed
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from db_pipeline import CSV_PATH, PRICE_BINS, PRICE_LABELS, clean_price


def load_data():
    """Read the CSV and add price tiers. Stops with a clear message if the file is missing."""
    try:
        df = pd.read_csv(CSV_PATH)
    except FileNotFoundError:
        raise SystemExit(f"Error: {CSV_PATH} not found. Put the CSV in the data/ folder.")
    df.columns = df.columns.str.strip().str.lower()
    df["price_numeric"] = df["price"].apply(clean_price)
    df["price_tier"] = pd.cut(df["price_numeric"], bins=PRICE_BINS, labels=PRICE_LABELS)
    df["has_recs"] = df["recommendations"] > 0
    return df


def price_tier_summary(df):
    return df.groupby("price_tier", observed=False).agg(
        games=("appid", "count"),
        avg_recs=("recommendations", "mean"),
        pct_with_recs=("has_recs", lambda s: s.mean() * 100),
    ).round(1)


def genre_summary(df):
    """One row per (game, genre) so 'Action;RPG' counts for both genres."""
    split = df.assign(genre=df["genres"].fillna("Uncategorized").str.split(";")).explode("genre")
    summary = split.groupby("genre").agg(
        games=("appid", "count"),
        avg_recs=("recommendations", "mean"),
        median_recs=("recommendations", "median"),
    ).round(1)
    return summary[summary["games"] >= 100].sort_values("avg_recs", ascending=False).head(10)


def save_charts(tiers):
    os.makedirs("data", exist_ok=True)
    sns.set_theme(style="darkgrid")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    sns.barplot(data=tiers.reset_index(), x="price_tier", y="avg_recs", hue="price_tier",
                palette="mako", legend=False, ax=axes[0])
    axes[0].set_title("Average recommendations by price tier")

    sns.barplot(data=tiers.reset_index(), x="price_tier", y="pct_with_recs", hue="price_tier",
                palette="rocket", legend=False, ax=axes[1])
    axes[1].set_title("% of games with at least 1 recommendation")

    for ax in axes:
        ax.set_xlabel("")
        ax.tick_params(axis="x", rotation=30)
    plt.tight_layout()
    plt.savefig("data/price_vs_recommendations.png", dpi=130)
    plt.close()


def main():
    print("Loading Steam dataset...")
    df = load_data()
    print(f"Loaded {len(df):,} games.\n")

    tiers = price_tier_summary(df)
    print("PRICE TIER SUMMARY")
    print(tiers.to_string(), "\n")

    print("TOP 10 GENRES (genres with 100+ games)")
    print(genre_summary(df).to_string(), "\n")

    save_charts(tiers)
    print("Chart saved as data/price_vs_recommendations.png")


if __name__ == "__main__":
    main()
