"""
Command-line database export utility. Exports every base table in the public schema to BACKUP_DIR as CSV by default, or as a pretty-formatted text
table when the format argument is pretty.
"""

import os
from argparse import ArgumentParser, Namespace
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pandas as pd
from pandas import DataFrame, Series
from tabulate import tabulate

from core.config import BACKUP_DIR
from core.utils.env import DATABASE_URL

parser: ArgumentParser = ArgumentParser()
parser.add_argument("format", nargs="?", choices=["csv", "pretty"], default="csv", )
args: Namespace = parser.parse_args()

os.makedirs(BACKUP_DIR, exist_ok=True)

print("Connecting to Supabase and fetching table list...")

try:
    tables: Series = pd.read_sql(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """,
        DATABASE_URL
    )['table_name']
    print(f"Found {len(tables)} tables to export.")

except Exception as e:
    print(f"Error: Failed to connect or fetch tables: {e}")
    exit(1)

for table in tables:
    print(f"Downloading table: {table}...")
    try:
        primary_key: str = cast("str", pd.read_sql(
            f"""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
            AND tc.table_name = '{table}'
            AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            LIMIT 1
            """,
            DATABASE_URL
        ).iloc[0, 0])

        df: DataFrame = pd.read_sql(
            f"""
            SELECT *
            FROM public.\"{table}\"
            ORDER BY \"{primary_key}\"
            """,
            DATABASE_URL
        )

        filename: Path
        if args.format == "pretty":
            filename = BACKUP_DIR / f"{table}.txt"
            filename.write_text(tabulate(cast("Iterable[Iterable[object]]", df), headers="keys", tablefmt="pretty", showindex=False), encoding="utf-8")

        else:
            filename = BACKUP_DIR / f"{table}.csv"
            df.to_csv(filename, index=False)

        print(f"Saved {len(df)} rows to {filename}")

    except Exception as e:
        print(f"Error exporting table {table}: {e}")

print(f"All table backup operations completed. Files are inside the '{BACKUP_DIR}' folder.")
