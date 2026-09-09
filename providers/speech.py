"""
VoiceBridge - 音声認識プロバイダー（言語自動判別対応、差し替え可能な設計）

ブラウザ自体の音声認識（Web Speech API）は無料だが、認識前に「どの言語で
話されるか」を指定する必要があり、真の意味での「話された言語の自動判別」はできない。

このプロバイダーは、Google Cloud Speech-to-Text を使い、
「候補となる2つの言語」を渡すことで、実際に話された言語を自動判別しながら
文字起こしする機能を提供する。

将来、別の音声認識サービスに切り替えたくなった場合、このファイルに新しいクラスを
追加し、get_speech_provider() の分岐を増やすだけで切り替えられるようにしている。
"""

import base64
import os
import subprocess


class GoogleSpeechProvider:
    """Google Cloud Speech-to-Text（REST API、APIキー認証）を使った音声認識プロバイダー"""

    ENDPOINT = "https://speech.googleapis.com/v1/speech:recognize"

    def __init__(self, api_key):
        self.api_key = api_key

    def recognize(self, audio_path, language_codes):
        """
        音声ファイルを、候補となる言語一覧（例: ["ja-JP", "en-US"]）の中から
        自動判別して文字起こしする。

        戻り値: (認識されたテキスト, 実際に判別された言語コード)
                認識できなかった場合は (None, None)
        """
        import requests

        # Google Speech-to-Textが安定して扱える形式（FLAC、16kHz、モノラル）に変換する
        flac_path = audio_path + ".flac"
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", flac_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"音声の変換に失敗しました: {result.stderr[-300:]}")

            with open(flac_path, "rb") as f:
                audio_content = base64.b64encode(f.read()).decode("utf-8")
        finally:
            if os.path.exists(flac_path):
                os.remove(flac_path)

        primary_language = language_codes[0]
        alternative_languages = language_codes[1:]

        response = requests.post(
            self.ENDPOINT,
            params={"key": self.api_key},
            json={
                "config": {
                    "encoding": "FLAC",
                    "sampleRateHertz": 16000,
                    "languageCode": primary_language,
                    "alternativeLanguageCodes": alternative_languages,
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": audio_content},
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        results = data.get("results")
        if not results:
            return None, None

        best_result = results[0]
        text = best_result["alternatives"][0]["transcript"]
        detected_language = best_result.get("languageCode", primary_language)
        return text, detected_language


def get_speech_provider():
    """
    環境変数 SPEECH_PROVIDER に応じて、使用する音声認識プロバイダーを選ぶ。
    未設定の場合は None を返す（＝この機能は無効。ブラウザの音声認識のみを使う）。
    """
    provider_name = os.environ.get("SPEECH_PROVIDER", "").lower()

    if not provider_name:
        return None

    if provider_name == "google":
        api_key = os.environ.get("GOOGLE_SPEECH_API_KEY")
        if not api_key:
            return None
        return GoogleSpeechProvider(api_key)

    raise ValueError(f"未対応の音声認識プロバイダーです: {provider_name}")
