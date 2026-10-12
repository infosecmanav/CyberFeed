"""Persist per-user report preferences locally, without credentials."""
import os
import sqlite3
from pathlib import Path
from .engine import ROOT


def connection():
    directory = Path(os.getenv('CYBERBRIEF_STATE_DIR', str(ROOT / '.state')))
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / 'preferences.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS preferences (user_id TEXT PRIMARY KEY, format TEXT NOT NULL)')
    return db


def get_format(user_id):
    db = connection()
    try:
        row = db.execute('SELECT format FROM preferences WHERE user_id = ?', (str(user_id),)).fetchone()
        return row[0] if row else 'text'
    finally:
        db.close()


def set_format(user_id, format):
    if format not in ('text', 'pdf'):
        raise ValueError('Choose text or pdf')
    db = connection()
    try:
        with db:
            db.execute('INSERT INTO preferences VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET format = excluded.format', (str(user_id), format))
    finally:
        db.close()
