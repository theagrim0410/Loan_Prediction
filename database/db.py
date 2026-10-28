"""Small SQLite data layer for prediction history and the training dataset."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "loan_approval.sqlite3"
DATASET_PATH = BASE_DIR / "dataset" / "loan_approval_data.csv"

DATASET_COLUMNS = (
    "applicant_id",
    "applicant_income",
    "coapplicant_income",
    "employment_status",
    "age",
    "marital_status",
    "dependents",
    "credit_score",
    "existing_loans",
    "dti_ratio",
    "savings",
    "collateral_value",
    "loan_amount",
    "loan_term",
    "loan_purpose",
    "property_area",
    "education_level",
    "gender",
    "employer_category",
    "loan_approved",
)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create all application tables and indexes if they do not exist."""
    with _connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS prediction_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                prediction TEXT NOT NULL CHECK (prediction IN ('Approved', 'Rejected')),
                confidence REAL,
                input_data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dataset_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id REAL,
                applicant_income REAL,
                coapplicant_income REAL,
                employment_status TEXT,
                age REAL,
                marital_status TEXT,
                dependents REAL,
                credit_score REAL,
                existing_loans REAL,
                dti_ratio REAL,
                savings REAL,
                collateral_value REAL,
                loan_amount REAL,
                loan_term REAL,
                loan_purpose TEXT,
                property_area TEXT,
                education_level TEXT,
                gender TEXT,
                employer_category TEXT,
                loan_approved TEXT,
                source TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(applicant_id, source)
            );

            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_prediction_history_created_at
                ON prediction_history(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dataset_records_approval
                ON dataset_records(loan_approved);
            """
        )


def record_prediction(
    input_data: dict[str, Any], prediction: str, confidence: float | None
) -> int:
    """Store one successful prediction and return its database id."""
    if prediction not in {"Approved", "Rejected"}:
        raise ValueError(f"Unsupported prediction value: {prediction!r}")

    init_db()
    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO prediction_history
                (created_at, prediction, confidence, input_data)
            VALUES (?, ?, ?, ?)
            """,
            (_now(), prediction, confidence, json.dumps(input_data)),
        )
        return int(cursor.lastrowid)


def get_prediction_history(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Return recent predictions with decoded input fields."""
    init_db()
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT id, created_at, prediction, confidence, input_data
            FROM prediction_history
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (safe_limit, safe_offset),
        ).fetchall()

    history = []
    for row in rows:
        item = dict(row)
        item["input_data"] = json.loads(item["input_data"])
        history.append(item)
    return history


def clear_prediction_history() -> int:
    """Delete prediction history and return the number of deleted rows."""
    init_db()
    with _connection() as connection:
        cursor = connection.execute("DELETE FROM prediction_history")
        return cursor.rowcount


def import_dataset() -> int:
    """Import the CSV dataset once, returning the number of inserted records."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset file not found: {DATASET_PATH}")

    init_db()
    inserted = 0
    with DATASET_PATH.open(newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        with _connection() as connection:
            for source_row in reader:
                row = {
                    "applicant_id": source_row.get("Applicant_ID"),
                    "applicant_income": source_row.get("Applicant_Income"),
                    "coapplicant_income": source_row.get("Coapplicant_Income"),
                    "employment_status": source_row.get("Employment_Status"),
                    "age": source_row.get("Age"),
                    "marital_status": source_row.get("Marital_Status"),
                    "dependents": source_row.get("Dependents"),
                    "credit_score": source_row.get("Credit_Score"),
                    "existing_loans": source_row.get("Existing_Loans"),
                    "dti_ratio": source_row.get("DTI_Ratio"),
                    "savings": source_row.get("Savings"),
                    "collateral_value": source_row.get("Collateral_Value"),
                    "loan_amount": source_row.get("Loan_Amount"),
                    "loan_term": source_row.get("Loan_Term"),
                    "loan_purpose": source_row.get("Loan_Purpose"),
                    "property_area": source_row.get("Property_Area"),
                    "education_level": source_row.get("Education_Level"),
                    "gender": source_row.get("Gender"),
                    "employer_category": source_row.get("Employer_Category"),
                    "loan_approved": source_row.get("Loan_Approved"),
                }
                values = [row[column] or None for column in DATASET_COLUMNS]
                cursor = connection.execute(
                    f"""
                    INSERT OR IGNORE INTO dataset_records
                        ({", ".join(DATASET_COLUMNS)}, source, imported_at)
                    VALUES ({", ".join("?" for _ in DATASET_COLUMNS)}, ?, ?)
                    """,
                    (*values, DATASET_PATH.name, _now()),
                )
                inserted += cursor.rowcount

    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO app_metadata (key, value, updated_at)
            VALUES ('dataset_source', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (DATASET_PATH.name, _now()),
        )
    return inserted


def ensure_dataset_loaded() -> int:
    """Ensure the CSV is represented in SQLite and return its record count."""
    init_db()
    with _connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM dataset_records").fetchone()[0]
    if count == 0:
        import_dataset()
        with _connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM dataset_records").fetchone()[0]
    return int(count)


def get_dataset(
    limit: int = 50, offset: int = 0, approval: str | None = None
) -> list[dict[str, Any]]:
    """Return dataset rows using pagination and an optional approval filter."""
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    query = "SELECT * FROM dataset_records"
    parameters: list[Any] = []
    if approval:
        query += " WHERE loan_approved = ?"
        parameters.append(approval)
    query += " ORDER BY id LIMIT ? OFFSET ?"
    parameters.extend((safe_limit, safe_offset))
    with _connection() as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def get_dataset_summary() -> dict[str, Any]:
    """Return useful counts for the dataset endpoint."""
    ensure_dataset_loaded()
    with _connection() as connection:
        total = connection.execute("SELECT COUNT(*) FROM dataset_records").fetchone()[0]
        approved = connection.execute(
            "SELECT COUNT(*) FROM dataset_records WHERE loan_approved = 'Yes'"
        ).fetchone()[0]
    return {
        "total_records": int(total),
        "approved_records": int(approved),
        "rejected_records": int(total - approved),
        "source": DATASET_PATH.name,
    }
