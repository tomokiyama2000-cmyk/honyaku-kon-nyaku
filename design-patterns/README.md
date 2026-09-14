# デザインパターン保管庫

VoiceBridge（ほんやくコンニャク）のUIデザインは、`static/style.css` を丸ごと差し替えることで
見た目を切り替えられるようになっています。気に入ったデザインは、このフォルダにスナップショットとして
保存しておくことで、後からいつでも呼び戻せます。

## 使い方

### 今のデザインを保存する
```
mkdir -p design-patterns/pattern番号-名前
cp static/style.css design-patterns/pattern番号-名前/style.css
```

### 過去のデザインを復元する
```
cp design-patterns/pattern番号-名前/style.css static/style.css
```
コピーしたら、GitHubにpushしてユーザー側で `git pull` すれば反映されます。

※ `templates/index.html` や `static/app.js` の構造（HTML要素・JS）は、どのパターンでも
共通で使えるように作ってあります。パターンごとに保存するのは `style.css` のみで十分です。
（将来、パターン固有のHTML構造が必要になった場合はその限りではありません）

## 保存済みのパターン一覧

| フォルダ名 | 通称 | 特徴 |
|---|---|---|
| `pattern2-pop-sticker` | ポップ・ステッカー系 | 太いアウトライン+ベタ塗りのオフセット影。紙工作・シールのような質感。タイトルバッジは傾いたシール風 |
