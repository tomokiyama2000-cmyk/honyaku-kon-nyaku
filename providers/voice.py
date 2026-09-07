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


class ElevenLabsVoiceProvider:
    """ElevenLabs公式APIを使った、声のクローン・音声合成プロバイダー"""

    MODEL_ID = "eleven_multilingual_v2"  # 29以上の言語に対応する多言語モデル

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

    def speak(self, text, voice_id, filepath, speed=1.0):
        """指定した声のクローン（voice_id）でテキストを読み上げ、音声ファイルとして保存する"""
        from elevenlabs import VoiceSettings

        audio_chunks = self._client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=self.MODEL_ID,
            output_format="mp3_44100_128",
            voice_settings=VoiceSettings(
                stability=0.5,  # 声の安定性（低いほど表現豊かだが不安定になりやすい）
                similarity_boost=0.85,  # 元の声にどれだけ似せるか（高いほど本人の声に近づく）
                style=0.0,
                use_speaker_boost=True,  # 声の明瞭さ・類似度を高める補正
                speed=speed,  # 読み上げ速度（1.0が標準）
            ),
        )
        with open(filepath, "wb") as f:
            for chunk in audio_chunks:
                f.write(chunk)

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
