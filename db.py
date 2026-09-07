"""
VoiceBridge - データベース処理（複数ユーザー対応の技術基盤）

SQLite（ファイル1つで完結する軽量なデータベース）を使い、
利用者ごとにアカウント・声のサンプル・会話履歴を分離して管理する。

テーブル構成：
    users   ：利用者のアカウント情報（ユーザー名、パスワードのハッシュ値など）
    history ：会話履歴（どの利用者のものか、user_id で紐づけられている）
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager

from werkzeug.security import check_password_hash, generate_password_hash


def get_db_path():
    """データベースファイルの保存場所を返す（環境変数 DATA_DIR に対応）"""
    data_dir = os.environ.get("DATA_DIR", os.path.dirname(__file__))
    return os.path.join(data_dir, "voicebridge.db")


@contextmanager
def get_connection():
    """データベースへの接続を開き、使い終わったら自動的に閉じる"""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row  # 列名でアクセスできるようにする
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """テーブルが無ければ作成する（アプリ起動時に毎回呼び出せば安全）"""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                voice_provider_id TEXT
            )
        """)
        # 古いバージョンで使っていた列名（elevenlabs_voice_id）からの移行や、
        # 後から列を追加した場合に備えて、失敗しても問題ない形で試しておく
        try:
            conn.execute("ALTER TABLE users RENAME COLUMN elevenlabs_voice_id TO voice_provider_id")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN voice_provider_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                source_language TEXT,
                target_language TEXT,
                original_text TEXT,
                translated_text TEXT,
                original_audio_url TEXT,
                translated_audio_url TEXT,
                voice_cloned INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)


# ---- ユーザー関連 ----

def create_user(username, password):
    """新しい利用者を登録する。ユーザー名が既に使われていたら None を返す。"""
    password_hash = generate_password_hash(password)
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, datetime('now'))",
                (username, password_hash),
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None  # ユーザー名が重複している


def verify_user(username, password):
    """ユーザー名とパスワードが正しければ、そのユーザーの情報を返す。間違っていれば None。"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return dict(row)
    return None


def get_user_by_id(user_id):
    """IDから利用者情報を取得する"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ---- 声のサンプルファイルのパス ----

def voice_sample_path_for_user(user_id):
    """指定した利用者専用の、声のサンプルファイルの保存場所を返す"""
    data_dir = os.environ.get("DATA_DIR", os.path.dirname(__file__))
    voice_sample_dir = os.path.join(data_dir, "voice_sample")
    os.makedirs(voice_sample_dir, exist_ok=True)
    return os.path.join(voice_sample_dir, f"user_{user_id}.wav")


def get_voice_provider_id(user_id):
    """指定した利用者の、声のクローンプロバイダー上に作成済みの声のID（voice_id）を取得する"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT voice_provider_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["voice_provider_id"] if row else None


def set_voice_provider_id(user_id, voice_id):
    """指定した利用者の、声のクローンプロバイダー上の声のID（voice_id）を保存する"""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET voice_provider_id = ? WHERE id = ?", (voice_id, user_id)
        )


# ---- 会話履歴関連（利用者ごとに分離） ----

def append_history_entry(user_id, entry):
    """指定した利用者の会話履歴に1件追加する"""
    entry_id = entry.get("id") or uuid.uuid4().hex
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO history (
                id, user_id, timestamp, source_language, target_language,
                original_text, translated_text, original_audio_url,
                translated_audio_url, voice_cloned
            ) VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, user_id, entry.get("source_language"), entry.get("target_language"),
                entry.get("original_text"), entry.get("translated_text"),
                entry.get("original_audio_url"), entry.get("translated_audio_url"),
                1 if entry.get("voice_cloned") else 0,
            ),
        )
    return entry_id


def get_history_for_user(user_id, limit=300):
    """指定した利用者の会話履歴を、直近のものから最大limit件、古い順に並べて取得する"""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT *, rowid AS _rowid FROM history WHERE user_id = ?
                ORDER BY timestamp DESC, _rowid DESC LIMIT ?
            ) AS recent
            ORDER BY timestamp ASC, _rowid ASC
            """,
            (user_id, limit),
        ).fetchall()
    return [_history_row_to_dict(row) for row in rows]


def get_history_entry(user_id, entry_id):
    """指定した利用者の、指定した1件の履歴を取得する（無ければ None）"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM history WHERE id = ? AND user_id = ?", (entry_id, user_id)
        ).fetchone()
    return _history_row_to_dict(row) if row else None


def delete_history_entry(user_id, entry_id):
    """
    指定した利用者の、指定した1件の履歴だけを削除する（他人の履歴は削除できない）。
    削除できた場合、その履歴が持っていた音声ファイルのURL一覧を返す
    （呼び出し側で、対応する音声ファイルを削除する際に使う）。
    削除対象が見つからなければ None を返す。
    """
    entry = get_history_entry(user_id, entry_id)
    if entry is None:
        return None

    with get_connection() as conn:
        conn.execute("DELETE FROM history WHERE id = ? AND user_id = ?", (entry_id, user_id))

    return [url for url in (entry["original_audio_url"], entry["translated_audio_url"]) if url]


def clear_history_for_user(user_id):
    """
    指定した利用者の会話履歴をすべて削除する。
    削除した全履歴が持っていた音声ファイルのURL一覧を返す。
    """
    entries = get_history_for_user(user_id, limit=100000)
    with get_connection() as conn:
        conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))

    audio_urls = []
    for entry in entries:
        audio_urls.extend(url for url in (entry["original_audio_url"], entry["translated_audio_url"]) if url)
    return audio_urls


def _history_row_to_dict(row):
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "source_language": row["source_language"],
        "target_language": row["target_language"],
        "original_text": row["original_text"],
        "translated_text": row["translated_text"],
        "original_audio_url": row["original_audio_url"],
        "translated_audio_url": row["translated_audio_url"],
        "voice_cloned": bool(row["voice_cloned"]),
    }
