"""
VoiceBridge - 発展課題3（拡張版4）: プロバイダー差し替え可能設計版

パソコンでもスマホでも、ブラウザからアクセスして使えるVoiceBridgeです。
画面上のボタンで自分の声を録音し、その声でクローン音声を生成できます。

仕組み：
- マイクの音声認識は、ブラウザ自体の機能（Web Speech API）を使う
- 翻訳・声のクローンは、それぞれ「プロバイダー」という部品に切り出してある
  （providers/translation.py, providers/voice.py）。
  現在は翻訳にDeepL、声のクローンにElevenLabsを使っているが、
  将来コストや品質の都合で別のサービスに切り替えたくなった場合も、
  この app.py 本体を書き換えずに、プロバイダーのファイルを追加するだけで対応できる。
- 「声を録音する」機能で、ブラウザから録音した音声をサーバーに送り、
  ffmpegでWAV形式に変換したうえで、声のクローンプロバイダーに登録する
- 「自分のクローン音声で読み上げる」にチェックを入れた時だけ、声のクローンを使う
  （チェックを外している間は、これまで通り高速なedge-ttsの自然な声を使う）

使い方：
    python3 app.py
    → 表示されるアドレス（例: http://127.0.0.1:5000）にブラウザでアクセスする
    → 同じWi-Fiに繋がっているスマホからも、パソコンのIPアドレスでアクセスできる

【重要】
- 声の録音・変換には、パソコンに ffmpeg がインストールされている必要があります。
- 翻訳には環境変数 DEEPL_API_KEY、声のクローンには環境変数 ELEVENLABS_API_KEY が必要です。
"""

import asyncio
import json
import os
import subprocess
import time
import uuid

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
import edge_tts

import db
from providers.translation import get_translation_provider
from providers.voice import get_voice_provider
from providers.speech import get_speech_provider

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "voicebridge-local-dev-secret")

# 翻訳・声のクローン・（自動言語判別の）音声認識の実際の処理は、それぞれのプロバイダーに任せる。
_translation_provider = get_translation_provider()
_voice_provider = get_voice_provider()
_speech_provider = get_speech_provider()  # 未設定の場合は None（ブラウザの音声認識のみを使う）

# アップロードできるファイルの最大サイズ（10MB）。
# 声の録音（15秒程度）は数百KB〜数MB程度で収まるため、これで十分な余裕がある。
# 上限を設けないと、不正または不具合のあるリクエストでディスクを圧迫されるおそれがある。
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

# セッションクッキーのセキュリティ設定
app.config["SESSION_COOKIE_HTTPONLY"] = True  # JavaScriptからクッキーを読めないようにする
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # 他サイトからの不正なリクエストを受けにくくする

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
    allowed_paths = ("/login", "/register", "/static/", "/sw.js")
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
        elif len(username) > 50:
            error = "ユーザー名は50文字以内にしてください。"
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

# 主要な言語の日本語名（表示用）。無ければ英語名だけを表示する。
LANGUAGE_JAPANESE_NAMES = {
    "en": "英語", "zh": "中国語", "es": "スペイン語", "hi": "ヒンディー語",
    "ar": "アラビア語", "pt": "ポルトガル語", "ru": "ロシア語", "ja": "日本語",
    "de": "ドイツ語", "fr": "フランス語", "ko": "韓国語", "it": "イタリア語",
    "tr": "トルコ語", "vi": "ベトナム語", "pl": "ポーランド語", "nl": "オランダ語",
    "id": "インドネシア語", "th": "タイ語", "sv": "スウェーデン語", "uk": "ウクライナ語",
    "el": "ギリシャ語", "cs": "チェコ語", "da": "デンマーク語", "fi": "フィンランド語",
    "hu": "ハンガリー語", "ro": "ルーマニア語", "sk": "スロバキア語", "bg": "ブルガリア語",
    "et": "エストニア語", "lv": "ラトビア語", "lt": "リトアニア語", "sl": "スロベニア語",
    "nb": "ノルウェー語", "nn": "ノルウェー語",
}

# 世界的な主要言語ほど上に表示されるようにするための優先順位（数字が小さいほど上位）。
# 話者数・国際的な使用頻度などを踏まえたおおよその目安。
LANGUAGE_PRIORITY_ORDER = [
    "en", "zh", "hi", "es", "ar", "fr", "pt", "ru", "ja", "de",
    "ko", "it", "tr", "vi", "pl", "nl", "id", "th", "sv", "uk",
    "el", "cs", "da", "fi", "hu", "ro", "sk", "bg", "et", "lv", "lt", "sl",
]


def _language_priority(lookup_code):
    """言語コードから、表示順を決めるための優先順位（数字）を返す"""
    try:
        return LANGUAGE_PRIORITY_ORDER.index(lookup_code)
    except ValueError:
        return len(LANGUAGE_PRIORITY_ORDER) + 1  # リストに無い言語は最後の方に表示する


def translate_with_retry(text, target_code, max_attempts=3):
    """翻訳プロバイダーを使ってテキストを翻訳する"""
    if _translation_provider is None:
        raise RuntimeError("翻訳サービスが設定されていません（APIキーの環境変数を確認してください）")
    return _translation_provider.translate(text, target_code, max_attempts=max_attempts)


async def build_language_table():
    """翻訳プロバイダーが対応する言語と、edge-ttsが対応する声を突き合わせて言語一覧を作る"""
    if _translation_provider is None:
        return {}

    target_languages = _translation_provider.get_languages()  # [(表示名, コード), ...]
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
    for name, code in target_languages:
        # 言語コードは "EN-US" や "PT-BR" のような地域付きの場合があるため、
        # 先頭部分（例: "en"）だけを取り出してedge-ttsの声と突き合わせる
        lookup_code = code.split("-")[0].lower()
        if lookup_code in voice_by_lang_code:
            # 表示名は「日本語（Japanese）」のように、日本語名と英語名を併記する
            # （日本語名が用意されていない言語は、英語名だけを表示する）
            japanese_name = LANGUAGE_JAPANESE_NAMES.get(lookup_code)
            display_name = f"{japanese_name}（{name}）" if japanese_name else name
            table[display_name] = {
                "translate_code": code,
                "sr_code": voice_by_lang_code[lookup_code]["locale"],
                "voice": voice_by_lang_code[lookup_code]["voice_name"],
                "priority": _language_priority(lookup_code),
            }
    return table


def clone_voice_from_sample(user_id, voice_sample_path):
    """
    声のサンプルファイルを使って、声のクローンプロバイダーに声を登録する。
    既にその利用者の声が登録済みの場合は、古いものを削除してから作り直す。
    作成した声のID（voice_id）をデータベースに保存して返す。
    """
    if _voice_provider is None:
        raise RuntimeError("声のクローンサービスが設定されていません（APIキーの環境変数を確認してください）")

    old_voice_id = db.get_voice_provider_id(user_id)
    voice_id = _voice_provider.clone_voice(user_id, voice_sample_path, old_voice_id=old_voice_id)
    db.set_voice_provider_id(user_id, voice_id)
    return voice_id


def speak_with_cloned_voice(text, voice_id, filepath, speed=None, language_code=None):
    """声のクローンプロバイダーを使って、テキストを読み上げた音声ファイルを作る"""
    _voice_provider.speak(text, voice_id, filepath, speed=speed, language_code=language_code)


@app.route("/")
def index():
    """トップページを表示する"""
    # 世界的な主要言語ほど上に表示されるように並び替える（同じ優先度なら表示名の順）
    language_names = sorted(
        LANGUAGE_TABLE.keys(),
        key=lambda name: (LANGUAGE_TABLE[name]["priority"], name),
    )
    has_voice_sample = db.get_voice_provider_id(session["user_id"]) is not None
    return render_template(
        "index.html",
        languages=language_names,
        language_table=LANGUAGE_TABLE,
        has_voice_sample=has_voice_sample,
        username=session.get("username"),
        auto_detect_available=_speech_provider is not None,
    )


@app.route("/api/recognize-audio", methods=["POST"])
def recognize_audio():
    """
    録音した音声から、2つの候補言語のどちらで話されたかを自動判別しながら文字起こしする。
    （Google Cloud Speech-to-Textなど、対応するプロバイダーが設定されている場合のみ使える）
    """
    if _speech_provider is None:
        return jsonify({"error": "自動言語判別のための音声認識サービスが設定されていません"}), 500

    if "audio" not in request.files:
        return jsonify({"error": "音声データが送られてきませんでした"}), 400

    language_codes_raw = request.form.get("language_codes", "")
    language_codes = [code.strip() for code in language_codes_raw.split(",") if code.strip()]
    if len(language_codes) < 2:
        return jsonify({"error": "2つの候補言語を指定してください"}), 400

    temp_path = os.path.join(AUDIO_DIR, f"_tmp_recognize_{uuid.uuid4().hex}.webm")
    request.files["audio"].save(temp_path)

    try:
        text, detected_language_code = _speech_provider.recognize(temp_path, language_codes)
    except Exception as e:
        return jsonify({"error": f"音声認識に失敗しました: {e}"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    if not text:
        return jsonify({"error": "音声を認識できませんでした。もう一度お試しください。"}), 200

    return jsonify({"text": text, "detected_language_code": detected_language_code})


@app.route("/api/save-voice-sample", methods=["POST"])
def save_voice_sample():
    """
    ブラウザで録音した音声を受け取り、WAV形式に変換したうえで、
    ElevenLabs上に「声のクローン」として登録する。
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
                "-ar", "44100", "-ac", "1",
                # ElevenLabsの公式ガイドラインによると、声のクローンは「デジタル処理の強さ」よりも
                # 「録音環境そのものの静かさ・明瞭さ」の方が重要とされている。過度な加工はかえって
                # 声の自然な特徴を損なう可能性があるため、最低限の処理（低音ノイズの除去）に留める。
                "-af", "highpass=f=80",
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

    # ElevenLabsに声のクローンとして登録する
    try:
        clone_voice_from_sample(session["user_id"], voice_sample_path)
    except Exception as e:
        return jsonify({"error": f"声のクローンの登録に失敗しました: {e}"}), 500

    return jsonify({"message": "声のサンプルを保存し、クローン音声を登録しました"})


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

    # クローン希望であれば、ElevenLabsに登録済みの声のIDを確認する
    voice_id = db.get_voice_provider_id(session["user_id"]) if want_clone_voice else None
    use_clone = want_clone_voice and voice_id is not None and _voice_provider is not None

    # 翻訳後の文章の音声を作る
    file_ext = "mp3"
    filename = f"{uuid.uuid4().hex}.{file_ext}"
    filepath = os.path.join(AUDIO_DIR, filename)

    try:
        if use_clone:
            # 翻訳文は少しゆっくりめに読み上げる（原文の言語より聞き取りにくいことが多いため）
            speak_with_cloned_voice(translated, voice_id, filepath, speed=0.85)
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
        use_clone_for_original = use_clone  # 翻訳文と同じ判定を使う（同じ声のクローンが使えるため）
        original_filename = f"{uuid.uuid4().hex}.mp3"
        original_filepath = os.path.join(AUDIO_DIR, original_filename)

        try:
            if use_clone_for_original:
                # 原文は、発音精度を上げるため言語を明示的に指定する（速度は指定せず自然な音質を優先）
                source_lookup_code = source_lang_info["translate_code"].split("-")[0].lower()
                speak_with_cloned_voice(text, voice_id, original_filepath, language_code=source_lookup_code)
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
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
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
    deleted_audio_urls = db.delete_history_entry(session["user_id"], entry_id)
    if deleted_audio_urls is None:
        return jsonify({"error": "該当する履歴が見つかりませんでした"}), 404
    _delete_audio_files(deleted_audio_urls)
    return jsonify({"message": "削除しました"})


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    """ログイン中の利用者の会話履歴をすべて削除する"""
    deleted_audio_urls = db.clear_history_for_user(session["user_id"])
    _delete_audio_files(deleted_audio_urls)
    return jsonify({"message": "履歴を削除しました"})


@app.route("/api/favorites", methods=["GET"])
def get_favorites():
    """ログイン中の利用者のお気に入りフレーズを返す"""
    return jsonify(db.get_favorites_for_user(session["user_id"]))


@app.route("/api/favorites", methods=["POST"])
def add_favorite_route():
    """よく使うフレーズをお気に入りに追加する"""
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "テキストが空です"}), 400
    if len(text) > 500:
        return jsonify({"error": "お気に入りに登録できるのは500文字までです"}), 400

    favorite_id = db.add_favorite(
        session["user_id"], text, data.get("source_language"), data.get("target_language")
    )
    return jsonify({"id": favorite_id, "text": text})


@app.route("/api/favorites/<favorite_id>", methods=["DELETE"])
def delete_favorite_route(favorite_id):
    """指定したお気に入りフレーズを削除する"""
    deleted = db.delete_favorite(session["user_id"], favorite_id)
    if not deleted:
        return jsonify({"error": "該当するお気に入りが見つかりませんでした"}), 404
    return jsonify({"message": "削除しました"})


def _delete_audio_files(audio_urls):
    """
    履歴の削除に伴って、不要になった音声ファイルをディスクから削除する。
    （削除しないままにしておくと、使われない音声ファイルが増え続けてしまうため）
    """
    for url in audio_urls:
        filename = os.path.basename(url)
        filepath = os.path.join(AUDIO_DIR, filename)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass  # ファイル削除に失敗しても、履歴の削除自体は成功として扱う


async def _speak_to_file(text, voice, filepath):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filepath)


@app.route("/static/generated_audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


@app.route("/sw.js")
def service_worker():
    """
    PWA（ホーム画面に追加できるアプリ）用のサービスワーカーを、サイト全体に
    適用される範囲（スコープ）で配信する。/static/ 以下から配信すると、
    その範囲にしか効かなくなってしまうため、ルート直下のパスで配信する。
    """
    return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")


@app.errorhandler(500)
def handle_server_error(e):
    """予期しないサーバーエラーが起きた場合、分かりやすいJSONで返す（API向け）"""
    return jsonify({"error": "サーバーで予期しないエラーが発生しました。もう一度お試しください。"}), 500


@app.errorhandler(413)
def handle_too_large(e):
    """アップロードされたファイルが大きすぎる場合、分かりやすいJSONで返す"""
    return jsonify({"error": "アップロードされたデータが大きすぎます。"}), 413


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
