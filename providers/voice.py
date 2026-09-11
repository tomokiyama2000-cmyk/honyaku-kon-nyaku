"""
VoiceBridge - 声のクローン・音声合成プロバイダー（差し替え可能な設計）

将来、ElevenLabsの費用が気になったり、別のサービス（あるいは無料の技術）に
切り替えたくなった場合、このファイルに新しいクラスを追加し、
get_voice_provider() の分岐を増やすだけで切り替えられるようにしている。

app.py（アプリ本体）は、ここで定義した共通のインターフェース
（clone_voice, speak, delete_voice）だけを使うため、
内部でどのサービスを使っているかを意識する必要がない。
"""

import os
import subprocess

# 声のトーン（話し方の雰囲気）のプリセット。
# stability: 声の安定性（低いほど抑揚が豊かで表現力が出る）
# style: 表現の強さ（高いほど感情がこもって聞こえる）
# speed: 読み上げ速度（1.0が標準）
# pitch_factor: 声の高さの倍率（1.0が変化なし。1.0より大きいと高く、小さいと低くなる）
VOICE_TONE_PRESETS = {
    "standard": {"stability": 0.4, "style": 0.35, "speed": None, "pitch_factor": None},
    "friendly": {"stability": 0.15, "style": 0.85, "speed": 1.08, "pitch_factor": 1.10},  # 明るい・テンション高め
    "business": {"stability": 0.80, "style": 0.0, "speed": 0.90, "pitch_factor": 0.90},  # 落ち着いた・まじめ
}
DEFAULT_VOICE_TONE = "standard"


def _shift_pitch(filepath, pitch_factor):
    """
    音声ファイルの再生時間（テンポ）は変えずに、声の高さだけを変える。
    （asetrateで再生速度ごと変えたあと、atempoで速度だけを元に戻すことで、
    　結果的に「高さだけ」が変わった音声にする、という定番のffmpegの手法）
    """
    temp_path = filepath + ".pitched.mp3"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", filepath,
                "-af", f"asetrate=44100*{pitch_factor},aresample=44100,atempo={1 / pitch_factor}",
		"-b:a", "192k", "-ar", "44100",
                temp_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(temp_path):
            os.replace(temp_path, filepath)
    except Exception:
        pass  # ピッチ変更に失敗しても、元の音声はそのまま使う
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


class ElevenLabsVoiceProvider:
    """ElevenLabs公式APIを使った、声のクローン・音声合成プロバイダー"""

    MODEL_ID = "eleven_multilingual_v2"  # 29以上の言語に対応する多言語モデル
    ACCURATE_MODEL_ID = "eleven_turbo_v2_5"  # 発音の言語を明示的に指定できるモデル

    def __init__(self, api_key):
        from elevenlabs.client import ElevenLabs
        self._client = ElevenLabs(api_key=api_key)

    def clone_voice(self, user_id, voice_sample_path, old_voice_id=None):
        """
        声のサンプルファイルから、ElevenLabs上に声のクローンを作成する。
        old_voice_id が指定されていれば、先にその声を削除してから作り直す
        （声が使われないまま増え続けるのを防ぐため）。
        作成された声のID（voice_id）を返す。
        """
        if old_voice_id:
            try:
                self._client.voices.delete(voice_id=old_voice_id)
            except Exception:
                pass  # 削除に失敗しても、新しい声の作成は続行する

        with open(voice_sample_path, "rb") as f:
            response = self._client.voices.ivc.create(
                name=f"voicebridge-user-{user_id}",
                files=[f],
                labels={},  # 省略すると、ライブラリ側の不具合でエラーになることがあるため明示的に空にする
            )
        return response.voice_id

    def speak(self, text, voice_id, filepath, speed=None, language_code=None, tone=DEFAULT_VOICE_TONE):
        """
        指定した声のクローン（voice_id）でテキストを読み上げ、音声ファイルとして保存する。

        tone には "standard"（標準）, "friendly"（明るい）, "business"（落ち着いた）
        のいずれかを指定でき、話し方の雰囲気を切り替えられる
        （安定性・表現の強さに加えて、話す速さ・声の高さも変えることで、はっきりと違いが分かるようにしている）。

        speed を明示的に指定した場合は、トーンの速度設定より優先される
        （原文・翻訳文で個別に速度を調整したい場合のため）。
        language_code を指定した場合、発音の精度を上げるために、通常の多言語モデルではなく
        「eleven_turbo_v2_5」という、言語を明示的に指定できる別のモデルを使う。
        """
        from elevenlabs import VoiceSettings

        tone_preset = VOICE_TONE_PRESETS.get(tone, VOICE_TONE_PRESETS[DEFAULT_VOICE_TONE])
        effective_speed = speed if speed is not None else tone_preset["speed"]

        settings_kwargs = {
            "stability": tone_preset["stability"],  # 声の安定性（低いほど抑揚が豊かになる）
            "similarity_boost": 0.85,  # 元の声にどれだけ似せるか（高いほど本人の声に近づく）
            "style": tone_preset["style"],  # 表現の強さ（トーンによって明るさ・まじめさを調整する）
            "use_speaker_boost": True,  # 声の明瞭さ・類似度を高める補正
        }
        if effective_speed is not None:
            settings_kwargs["speed"] = effective_speed

        convert_kwargs = {
            "voice_id": voice_id,
            "text": text,
            "output_format": "mp3_44100_128",
            "voice_settings": VoiceSettings(**settings_kwargs),
        }

        if language_code:
            # 言語を明示的に指定できるモデルに切り替え、発音精度を上げる
            convert_kwargs["model_id"] = self.ACCURATE_MODEL_ID
            convert_kwargs["language_code"] = language_code
            # 日本語の発音をより正確にするための正規化（他の言語には影響しない）
            convert_kwargs["apply_language_text_normalization"] = True
        else:
            convert_kwargs["model_id"] = self.MODEL_ID

        audio_chunks = self._client.text_to_speech.convert(**convert_kwargs)
        with open(filepath, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

        # トーンに応じて、声の高さも変える（大幅に印象を変えるため）
        if tone_preset["pitch_factor"] is not None:
            _shift_pitch(filepath, tone_preset["pitch_factor"])

    def delete_voice(self, voice_id):
        """指定した声のクローンを削除する"""
        self._client.voices.delete(voice_id=voice_id)


def get_voice_provider():
    """
    環境変数 VOICE_PROVIDER に応じて、使用する声のクローンプロバイダーを選ぶ。
    （未設定の場合は "elevenlabs" を使う）

    将来、別のサービスや無料の技術に切り替えたい場合：
      1. このファイルに新しいクラス（例: XTTSVoiceProvider）を追加する
         （clone_voice, speak, delete_voice の3つのメソッドを実装する）
      2. 下の if 文に分岐を追加する
      3. 環境変数 VOICE_PROVIDER の値を変えるだけで切り替えられる
    """
    provider_name = os.environ.get("VOICE_PROVIDER", "elevenlabs").lower()

    if provider_name == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return None
        return ElevenLabsVoiceProvider(api_key)

    raise ValueError(f"未対応の声のクローンプロバイダーです: {provider_name}")
