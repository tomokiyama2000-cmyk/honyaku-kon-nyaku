"""
VoiceBridge - 翻訳プロバイダー（差し替え可能な設計）

将来、DeepL以外の翻訳サービスに切り替えたくなった場合、
このファイルに新しいクラスを追加し、get_translation_provider() の
分岐を増やすだけで切り替えられるようにしている。

app.py（アプリ本体）は、ここで定義した共通のインターフェース
（get_languages, translate）だけを使うため、
内部でどのサービスを使っているかを意識する必要がない。
"""

import os
import time


class DeepLTranslationProvider:
    """DeepL公式APIを使った翻訳プロバイダー"""

    def __init__(self, api_key):
        import deepl
        self._translator = deepl.Translator(api_key)

    def get_languages(self):
        """(表示名, 翻訳先の言語コード) のリストを返す"""
        return [(lang.name, lang.code) for lang in self._translator.get_target_languages()]

    def translate(self, text, target_code, max_attempts=3):
        """
        テキストを翻訳する。一時的な通信不調に備えて、
        少し待ってから自動的に再試行する。
        """
        last_error = None
        for attempt in range(max_attempts):
            try:
                result = self._translator.translate_text(text, target_lang=target_code)
                return result.text
            except Exception as e:
                last_error = e
                time.sleep(1)
        raise last_error


def get_translation_provider():
    """
    環境変数 TRANSLATION_PROVIDER に応じて、使用する翻訳プロバイダーを選ぶ。
    （未設定の場合は "deepl" を使う）

    将来、別の翻訳サービスに切り替えたい場合：
      1. このファイルに新しいクラス（例: GoogleTranslationProvider）を追加する
      2. 下の if 文に分岐を追加する
      3. 環境変数 TRANSLATION_PROVIDER の値を変えるだけで切り替えられる
    """
    provider_name = os.environ.get("TRANSLATION_PROVIDER", "deepl").lower()

    if provider_name == "deepl":
        api_key = os.environ.get("DEEPL_API_KEY")
        if not api_key:
            return None
        return DeepLTranslationProvider(api_key)

    raise ValueError(f"未対応の翻訳プロバイダーです: {provider_name}")
