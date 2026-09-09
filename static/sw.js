// VoiceBridge（ほんやくコンニャク）- PWA用サービスワーカー
//
// このファイルは「スマホのホーム画面にアプリのように追加できる」ようにするための、
// 最小限の仕組み（サービスワーカー）です。
// 会話内容は毎回サーバーと通信して処理する必要があるアプリのため、
// 本格的なオフライン対応（キャッシュ）はせず、インストール可能にすることだけを目的にしている。

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

// fetchイベントを一応ハンドリングしておく（ブラウザがPWAとして認識するための要件のため）。
// 実際の処理は何もせず、通常通りネットワークから取得する。
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
