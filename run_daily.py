from src.pipeline import run_pipeline

if __name__ == "__main__":
    rows_seen, rows_upserted = run_pipeline()
    print(f"Done. Rows seen: {rows_seen}. Rows upserted: {rows_upserted}.")
