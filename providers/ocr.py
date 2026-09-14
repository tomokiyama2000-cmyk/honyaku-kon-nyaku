"""
VoiceBridge - 文字認識（OCR）プロバイダー（差し替え可能な設計）

カメラで撮影した写真やスクリーンショットから文字を読み取る機能を提供する。
既存の音声認識（Google Cloud Speech-to-Text）と同じGoogle Cloudプロジェクトで
Vision APIも有効化すれば、同じAPIキーをそのまま使い回せる想定。

将来、別のOCRサービスに切り替えたくなった場合、このファイルに新しいクラスを
追加し、get_ocr_provider() の分岐を増やすだけで切り替えられるようにしている。
"""

import base64
import os


class GoogleVisionOCRProvider:
    """Google Cloud Vision API（REST API、APIキー認証）を使った文字認識プロバイダー"""

    ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self, api_key):
        self.api_key = api_key

    def extract_text(self, image_bytes):
        """
        画像データ（バイト列）から文字を読み取る。

        戻り値: (読み取ったテキスト, 検出された言語コード)
                文字が見つからなかった場合は (None, None)
        """
        import requests

        image_content = base64.b64encode(image_bytes).decode("utf-8")

        response = requests.post(
            self.ENDPOINT,
            params={"key": self.api_key},
            json={
                "requests": [
                    {
                        "image": {"content": image_content},
                        "features": [{"type": "TEXT_DETECTION"}],
                    }
                ]
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        result = (data.get("responses") or [{}])[0]
        if "error" in result:
            raise RuntimeError(result["error"].get("message", "文字認識に失敗しました"))

        annotations = result.get("textAnnotations")
        if not annotations:
            return None, None

        # 先頭の要素が「画像全体から読み取れたテキスト全部」を表す
        full_text = annotations[0]["description"]
        detected_language = None
        text_props = result.get("fullTextAnnotation", {}).get("pages", [{}])[0].get("property", {})
        detected_languages = text_props.get("detectedLanguages")
        if detected_languages:
            detected_language = detected_languages[0].get("languageCode")

        return full_text, detected_language


def get_ocr_provider():
    """
    環境変数 OCR_PROVIDER に応じて、使用するOCRプロバイダーを選ぶ。
    未設定の場合は None を返す（＝カメラ翻訳機能は無効）。
    APIキーは専用の GOOGLE_VISION_API_KEY があればそれを使い、無ければ
    音声認識で使っている GOOGLE_SPEECH_API_KEY を代わりに使う（同じGoogle Cloud
    プロジェクトでVision APIも有効化している場合に使い回せるようにするため）。
    """
    provider_name = os.environ.get("OCR_PROVIDER", "google").lower()

    if not provider_name or provider_name == "none":
        return None

    if provider_name == "google":
        api_key = os.environ.get("GOOGLE_VISION_API_KEY") or os.environ.get("GOOGLE_SPEECH_API_KEY")
        if not api_key:
            return None
        return GoogleVisionOCRProvider(api_key)

    raise ValueError(f"未対応のOCRプロバイダーです: {provider_name}")
