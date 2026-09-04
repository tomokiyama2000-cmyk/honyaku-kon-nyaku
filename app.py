"""
VoiceBridge - 発展課題3（拡張版2）: アプリ内で声を録音してクローンできる版

パソコンでもスマホでも、ブラウザからアクセスして使えるVoiceBridgeです。
画面上のボタンで自分の声を録音し、その声でクローン音声を生成できます。

仕組み：
- マイクの音声認識は、ブラウザ自体の機能（Web Speech API）を使う
- 「声を録音する」機能で、ブラウザから録音した音声をサーバーに送り、
  ffmpegでWAV形式に変換して声のサンプルとして保存する
- 「自分の声で読み上げる」にチェックを入れた時だけ、声のクローン（Coqui XTTS）を使う
  （チェックを外している間は、これまで通り高速なedge-ttsの自然な声を使う）
- 声のクローンAIモデルは、実際に必要になった最初のタイミングで読み込む（起動を速くするため）

使い方：
    python3 app.py
    → 表示されるアドレス（例: http://127.0.0.1:5000）にブラウザでアクセスする
    → 同じWi-Fiに繋がっているスマホからも、パソコンのIPアドレスでアクセスできる

【重要】
- 声の録音・変換には、パソコンに ffmpeg がインストールされている必要があります。
- 声のクローンを初めて使うとき、AIモデルの読み込みに時間がかかります（それ以降は速くなります）。
"""

import asyncio
import json
import os
import subprocess
import time
import uuid

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from deep_translator import GoogleTranslator
import edge_tts

import db

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "voicebridge-local-dev-secret")

db.init_db()

# 永続化したいデータ（声のサンプル・生成音声・履歴）の保存先。
# Renderで永続ディスク（Persistent Disk）を使う場合は、環境変数 DATA_DIR で
# ディスクのマウント先（例: /var/data）を指定してください。
DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))

# 生成した音声ファイルを保存する場所
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "generated_audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


# ---- アカウント登録・ログイン（利用者ごとにアカウントを持つ） ----
@app.before_request
def require_login():
    allowed_paths = ("/login", "/register", "/static/")
    if request.path.startswith(allowed_paths):
        return
    if not session.get("user_id"):
        return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password_confirm = request.form.get("password_confirm") or ""

        if not username or not password:
            error = "ユーザー名とパスワードを入力してください。"
        elif password != password_confirm:
            error = "パスワードが一致しません。"
        elif len(password) < 6:
            error = "パスワードは6文字以上にしてください。"
        else:
            user_id = db.create_user(username, password)
            if user_id is None:
                error = "そのユーザー名はすでに使われています。"
            else:
                session["user_id"] = user_id
                session["username"] = username
                return redirect(url_for("index"))

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = db.verify_user(username, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        error = "ユーザー名またはパスワードが違います。"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))






# 対応言語一覧（起動時に一度だけ作成してキャッシュしておく）
LANGUAGE_TABLE = {}

# 声のクローン（XTTS）モデル。最初に必要になったタイミングで読み込む（起動を速くするため）
XTTS_MODEL = None
XTTS_LOAD_FAILED = False

# XTTSが対応している言語コード（これ以外の言語は自動的にedge-ttsにフォールバックする）
XTTS_SUPPORTED_LANGS = {
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi",
}


# 翻訳サービスが一時的に不調な時、エラーページの中身を「翻訳結果」として
# 誤って返してしまうことがある。それを見分けるための、よくあるエラー文言の目印。
_TRANSLATION_ERROR_MARKERS = (
    "that's an error", "there was an error", "error 500", "error 404",
    "<html", "<!doctype",
)


def looks_like_translation_error(text):
    """翻訳結果が、実はエラーページの中身だった場合に True を返す"""
    lowered = text.lower()
    return any(marker in lowered for marker in _TRANSLATION_ERROR_MARKERS)


def translate_with_retry(text, target_code, max_attempts=3):
    """
    翻訳を実行する。一時的な不調（エラーページが返ってくる、接続できない等）の場合、
    少し待ってから自動的に再試行する。
    """
    last_error = None
    for attempt in range(max_attempts):
        try:
            result = GoogleTranslator(source="auto", target=target_code).translate(text)
        except Exception as e:
            last_error = e
            time.sleep(1)
            continue

        if result and result.strip() and not looks_like_translation_error(result):
            return result

        last_error = RuntimeError("翻訳サービスから正しい結果が返ってきませんでした")
        time.sleep(1)

    raise last_error


async def build_language_table():
    """翻訳が対応する言語と、edge-ttsが対応する声を突き合わせて言語一覧を作る"""
    translate_langs = GoogleTranslator().get_supported_languages(as_dict=True)
    all_voices = await edge_tts.list_voices()

    voice_by_lang_code = {}
    for voice in all_voices:
        locale = voice["Locale"]
        lang_code = locale.split("-")[0]
        if lang_code not in voice_by_lang_code:
            voice_by_lang_code[lang_code] = {
                "locale": locale,
                "voice_name": voice["ShortName"],
            }

    table = {}
    for name, code in translate_langs.items():
        lookup_code = code.split("-")[0]
        if lookup_code in voice_by_lang_code:
            table[name] = {
                "translate_code": code,
                "sr_code": voice_by_lang_code[lookup_code]["locale"],
                "voice": voice_by_lang_code[lookup_code]["voice_name"],
                "xtts_code": code.lower() if code.lower() in XTTS_SUPPORTED_LANGS else None,
            }
    return table


def ensure_xtts_loaded():
    """声のクローン（XTTS）モデルを、まだ読み込んでいなければ読み込む（最初の1回だけ時間がかかる）"""
    global XTTS_MODEL, XTTS_LOAD_FAILED

    if XTTS_MODEL is not None or XTTS_LOAD_FAILED:
        return XTTS_MODEL

    try:
        from TTS.api import TTS
        print("声のクローンAIモデルを読み込んでいます（初回のみ、少し時間がかかります）...")
        XTTS_MODEL = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        print("声のクローンAIモデルの読み込みが完了しました。")
    except Exception as e:
        print(f"声のクローンAIモデルの読み込みに失敗しました: {e}")
        XTTS_LOAD_FAILED = True

    return XTTS_MODEL


@app.route("/")
def index():
    """トップページを表示する"""
    language_names = sorted(LANGUAGE_TABLE.keys())
    voice_sample_path = db.voice_sample_path_for_user(session["user_id"])
    has_voice_sample = os.path.exists(voice_sample_path)
    return render_template(
        "index.html",
        languages=language_names,
        language_table=LANGUAGE_TABLE,
        has_voice_sample=has_voice_sample,
        username=session.get("username"),
    )


@app.route("/api/save-voice-sample", methods=["POST"])
def save_voice_sample():
    """
    ブラウザで録音した音声を受け取り、ログイン中の利用者専用の声のサンプルとして保存する。
    ブラウザは webm 形式などで録音するため、ffmpeg を使ってXTTSが使いやすいWAV形式に変換する。
    """
    if "audio" not in request.files:
        return jsonify({"error": "音声データが送られてきませんでした"}), 400

    voice_sample_path = db.voice_sample_path_for_user(session["user_id"])
    voice_sample_dir = os.path.dirname(voice_sample_path)

    audio_file = request.files["audio"]
    temp_path = os.path.join(voice_sample_dir, f"_tmp_{uuid.uuid4().hex}.webm")
    audio_file.save(temp_path)

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", temp_path,
                "-ar", "24000", "-ac", "1",
                # 音声フィルタを順番に適用する：
                # 1. highpass: 120Hz以下の低い音（鼻息や部屋の低いノイズなど）をカットする
                # 2. afftdn: 全体的な背景ノイズを軽減する
                # 3. silenceremove: 無音区間（間）を自動的に取り除く。声のクローンが「間」の
                #    話し方を真似してしまうのを防ぎ、より流ちょうな読み上げにするため
                "-af", "highpass=f=120,afftdn=nf=-25,"
                       "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-35dB:"
                       "stop_periods=-1:stop_duration=0.3:stop_threshold=-35dB",
                voice_sample_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return jsonify({"error": f"音声の変換に失敗しました: {result.stderr[-300:]}"}), 500
    except FileNotFoundError:
        return jsonify({"error": "ffmpegが見つかりません。パソコンにffmpegをインストールしてください。"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "音声の変換がタイムアウトしました"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return jsonify({"message": "声のサンプルを保存しました"})


@app.route("/api/process", methods=["POST"])
def process():
    """ブラウザから送られてきたテキストを翻訳し、原文・翻訳文それぞれの音声ファイルを生成して返すAPI。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "リクエストの形式が正しくありません"}), 400

    text = (data.get("text") or "").strip()
    source_language_name = data.get("source_language")
    target_language_name = data.get("target_language")
    want_clone_voice = bool(data.get("use_clone", False))

    if not text:
        return jsonify({"error": "テキストが空です"}), 400

    lang_info = LANGUAGE_TABLE.get(target_language_name)
    if not lang_info:
        return jsonify({"error": "対応していない言語です。ページを再読み込みしてもう一度お試しください。"}), 400

    source_lang_info = LANGUAGE_TABLE.get(source_language_name)

    # 翻訳（一時的な不調の場合は自動的に再試行する）
    try:
        translated = translate_with_retry(text, lang_info["translate_code"])
    except Exception as e:
        return jsonify({"error": f"翻訳サービスに接続できませんでした。もう一度お試しください。（詳細: {e}）"}), 500

    if not translated or not translated.strip():
        return jsonify({"error": "翻訳結果が空でした。もう一度お試しください。"}), 500

    voice_sample_path = db.voice_sample_path_for_user(session["user_id"])
    has_voice_sample = os.path.exists(voice_sample_path)

    # クローン希望で、かつ翻訳先か原文どちらかの言語がXTTSに対応していれば、モデルを読み込む
    source_supports_clone = source_lang_info is not None and source_lang_info["xtts_code"] is not None
    target_supports_clone = lang_info["xtts_code"] is not None
    needs_model = want_clone_voice and has_voice_sample and (source_supports_clone or target_supports_clone)

    model = None
    if needs_model:
        try:
            model = ensure_xtts_loaded()
        except Exception:
            model = None

    use_clone = want_clone_voice and model is not None and target_supports_clone

    # 翻訳後の文章の音声を作る
    file_ext = "wav" if use_clone else "mp3"
    filename = f"{uuid.uuid4().hex}.{file_ext}"
    filepath = os.path.join(AUDIO_DIR, filename)

    try:
        if use_clone:
            model.tts_to_file(
                text=translated,
                speaker_wav=voice_sample_path,
                language=lang_info["xtts_code"],
                file_path=filepath,
                split_sentences=True,  # 長い文章は文ごとに区切って生成し、自然さを保ちやすくする
                speed=0.95,  # 少しゆっくりめにして、発音の崩れを抑える
            )
            trim_trailing_noise(filepath)  # 末尾の不要な間・ノイズを取り除く
        else:
            asyncio.run(_speak_to_file(translated, lang_info["voice"], filepath))
    except Exception as e:
        # 声のクローンで失敗した場合は、自然な声（edge-tts）で再挑戦してみる
        if use_clone:
            try:
                filename = f"{uuid.uuid4().hex}.mp3"
                filepath = os.path.join(AUDIO_DIR, filename)
                asyncio.run(_speak_to_file(translated, lang_info["voice"], filepath))
                use_clone = False
            except Exception as e2:
                return jsonify({"error": f"音声合成に失敗しました: {e2}"}), 500
        else:
            return jsonify({"error": f"音声合成に失敗しました: {e}"}), 500

    # 原文の音声も作る（再生できるように）。翻訳文と同じく、チェックが入っていればクローン音声を使う。
    original_audio_url = None
    if source_lang_info:
        use_clone_for_original = (
            want_clone_voice and model is not None and source_lang_info["xtts_code"] is not None
        )
        original_ext = "wav" if use_clone_for_original else "mp3"
        original_filename = f"{uuid.uuid4().hex}.{original_ext}"
        original_filepath = os.path.join(AUDIO_DIR, original_filename)

        try:
            if use_clone_for_original:
                model.tts_to_file(
                    text=text,
                    speaker_wav=voice_sample_path,
                    language=source_lang_info["xtts_code"],
                    file_path=original_filepath,
                    split_sentences=True,
                    speed=0.95,
                )
                trim_trailing_noise(original_filepath)  # 末尾の不要な間・ノイズを取り除く
            else:
                asyncio.run(_speak_to_file(text, source_lang_info["voice"], original_filepath))
            original_audio_url = f"/static/generated_audio/{original_filename}"
        except Exception:
            # クローンで失敗したら、自然な声で再挑戦してみる
            try:
                original_filename = f"{uuid.uuid4().hex}.mp3"
                original_filepath = os.path.join(AUDIO_DIR, original_filename)
                asyncio.run(_speak_to_file(text, source_lang_info["voice"], original_filepath))
                original_audio_url = f"/static/generated_audio/{original_filename}"
            except Exception:
                original_audio_url = None  # それでも失敗したら、原文の音声は無しで翻訳結果は返す

    history_entry = {
        "id": uuid.uuid4().hex,
        "source_language": source_language_name,
        "target_language": target_language_name,
        "original_text": text,
        "translated_text": translated,
        "original_audio_url": original_audio_url,
        "translated_audio_url": f"/static/generated_audio/{filename}",
        "voice_cloned": use_clone,
    }
    try:
        db.append_history_entry(session["user_id"], history_entry)
    except Exception:
        pass  # 履歴の保存に失敗しても、翻訳結果自体は返す

    return jsonify({
        "id": history_entry["id"],
        "original_text": text,
        "translated_text": translated,
        "original_audio_url": original_audio_url,
        "translated_audio_url": f"/static/generated_audio/{filename}",
        "voice_cloned": use_clone,
    })


@app.route("/api/history")
def get_history():
    """ログイン中の利用者の会話履歴を返す"""
    return jsonify(db.get_history_for_user(session["user_id"]))


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def delete_history_entry_route(entry_id):
    """ログイン中の利用者の履歴から、指定した1件だけを削除する（他人の履歴は削除できない）"""
    deleted = db.delete_history_entry(session["user_id"], entry_id)
    if not deleted:
        return jsonify({"error": "該当する履歴が見つかりませんでした"}), 404
    return jsonify({"message": "削除しました"})


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    """ログイン中の利用者の会話履歴をすべて削除する"""
    db.clear_history_for_user(session["user_id"])
    return jsonify({"message": "履歴を削除しました"})


async def _speak_to_file(text, voice, filepath):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filepath)


def trim_trailing_noise(filepath):
    """
    声のクローン音声の末尾に残りがちな、不要な無音・ノイズを取り除く。
    （XTTSは文章の最後に、わずかな「間」や雑音を生成することがあるため）

    音声を一度逆再生の状態にしてから、その「先頭」（＝元の音声では「末尾」）の
    無音だけを取り除き、また元の向きに戻す、という方法を使う。
    こうすることで、文章の途中にある間（区切りの間）は保持したまま、
    本当に末尾だけを安全にトリミングできる。
    """
    temp_path = filepath + ".trimmed.wav"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", filepath,
                "-af", "areverse,"
                       "silenceremove=start_periods=1:start_duration=0:start_threshold=-40dB:detection=peak,"
                       "areverse,"
                       "afade=t=out:d=0.05",  # ほんの一瞬フェードアウトさせ、切れ目のプツッという音を防ぐ
                temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.exists(temp_path):
            os.replace(temp_path, filepath)
    except Exception:
        pass  # トリミングに失敗しても、元の音声はそのまま使う
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@app.route("/static/generated_audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


@app.errorhandler(500)
def handle_server_error(e):
    """予期しないサーバーエラーが起きた場合、分かりやすいJSONで返す（API向け）"""
    return jsonify({"error": "サーバーで予期しないエラーが発生しました。もう一度お試しください。"}), 500


if __name__ == "__main__":
    print("対応言語を準備しています（初回のみ、少し時間がかかります）...")
    LANGUAGE_TABLE = asyncio.run(build_language_table())
    print(f"準備できました。対応言語数: {len(LANGUAGE_TABLE)} 言語")
    print("アカウント登録・ログインが必要です。")
    print("下に表示されるアドレスにブラウザでアクセスしてください。")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # gunicorn などの本番用サーバーからモジュールとして読み込まれた場合も、
    # 対応言語の準備を行っておく（"python app.py" 以外の起動方法に対応するため）
    print("対応言語を準備しています（初回のみ、少し時間がかかります）...")
    try:
        LANGUAGE_TABLE = asyncio.run(build_language_table())
        print(f"準備できました。対応言語数: {len(LANGUAGE_TABLE)} 言語")
    except Exception as e:
        # 起動時に一時的なネットワーク不調があっても、アプリ全体がクラッシュしないようにする
        print(f"対応言語の準備に失敗しました（起動時の一時的な問題の可能性）: {e}")
        LANGUAGE_TABLE = {}
