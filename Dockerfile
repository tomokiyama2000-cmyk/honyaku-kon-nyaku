# VoiceBridge（ほんやくコンニャク）をクラウド上で動かすための環境構築手順書。
# Renderなどのサービスが、この内容通りにサーバーの中身を組み立ててくれます。

FROM python:3.12-slim

# 音声の変換に必要なffmpegをインストールする
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先にライブラリの一覧だけコピーしてインストールする
# （こうしておくと、コードだけ変更した時の再ビルドが速くなる）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体のファイルをコピーする
COPY . .

# Renderはこの環境変数でポート番号を指定してくる。無ければ5000番を使う
ENV PORT=5000
EXPOSE 5000

# 本番用のサーバー（gunicorn）でアプリを起動する。
# 1回のリクエストで翻訳・声のクローン・音声認識と複数の外部APIを順番に呼ぶことがあるため、
# タイムアウトを少し余裕を持たせている。
CMD gunicorn --workers 1 --timeout 90 --bind 0.0.0.0:$PORT app:app
