// VoiceBridge - ブラウザ側の動作
// ブラウザ自体のマイク機能（Web Speech API）を使って音声認識し、
// 認識結果をサーバーに送って翻訳・音声合成してもらう。

const micButton = document.getElementById("micButton");
const statusText = document.getElementById("statusText");
const waveform = document.getElementById("waveform");
const sourceLanguageSelect = document.getElementById("sourceLanguage");
const targetLanguageSelect = document.getElementById("targetLanguage");
const swapButton = document.getElementById("swapButton");
const transcript = document.getElementById("transcript");
const player = document.getElementById("player");
const useCloneCheckbox = document.getElementById("useCloneCheckbox");
const manualModeCheckbox = document.getElementById("manualModeCheckbox");
const conversationModeCheckbox = document.getElementById("conversationModeCheckbox");
const autoDetectCheckbox = document.getElementById("autoDetectCheckbox");
const handsFreeCheckbox = document.getElementById("handsFreeCheckbox");
const toneOptions = document.querySelectorAll(".tone-option");
const recordSampleButton = document.getElementById("recordSampleButton");
const recordButtonLabel = document.getElementById("recordButtonLabel");
const recordProgress = document.getElementById("recordProgress");
const recordStatusText = document.getElementById("recordStatusText");
const voiceSampleStatus = document.getElementById("voiceSampleStatus");
const textInputForm = document.getElementById("textInputForm");
const textInput = document.getElementById("textInput");
const clearHistoryButton = document.getElementById("clearHistoryButton");
const toggleHistoryButton = document.getElementById("toggleHistoryButton");
const toggleHistoryButtonLabel = document.getElementById("toggleHistoryButtonLabel");
const latestResult = document.getElementById("latestResult");
const themeToggleButton = document.getElementById("themeToggleButton");
const themeIconSun = document.getElementById("themeIconSun");
const themeIconMoon = document.getElementById("themeIconMoon");
const helpButton = document.getElementById("helpButton");
const quickPairs = document.getElementById("quickPairs");
const favoritesRow = document.getElementById("favoritesRow");
const tutorialOverlay = document.getElementById("tutorialOverlay");
const tutorialSteps = document.getElementById("tutorialSteps");
const tutorialDots = document.getElementById("tutorialDots");
const tutorialNext = document.getElementById("tutorialNext");
const tutorialSkip = document.getElementById("tutorialSkip");

let hasVoiceSample = recordSampleButton.classList.contains("record-button--subtle");

function resetRecordButtonLabel() {
  recordButtonLabel.textContent = hasVoiceSample ? "声を録音し直す" : "🎙️ 声を録音する（60秒）";
}

// ブラウザの音声認識機能を用意する（Chrome系ブラウザで利用可能）
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;

if (!SpeechRecognition) {
  statusText.textContent =
    "このブラウザは音声認識に対応していません。Google Chromeでのご利用をおすすめします。";
  micButton.disabled = true;
} else {
  recognition = new SpeechRecognition();
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
}

let isListening = false;
let accumulatedText = "";

// 自動言語判別モード用（Web Speech APIは「発話の終わり」検知だけに使い、
// 実際の文字起こしはGoogle Cloud Speech-to-Textに任せる）
let detectionMediaRecorder = null;
let detectionChunks = [];
let detectionStream = null;

function localeForLanguageName(name) {
  // サーバーから埋め込まれた対応言語表を使って、選ばれた言語名から
  // ブラウザの音声認識用ロケール（例: "ja-JP"）を調べる
  const info = window.LANGUAGE_TABLE[name];
  return info ? info.sr_code : "ja-JP";
}

function startListening() {
  if (!recognition || isListening) return;

  if (autoDetectCheckbox.checked) {
    startListeningWithAutoDetect();
    return;
  }

  const manualMode = manualModeCheckbox.checked;
  // 手動モード：話の途中で無音になっても自動終了しない（continuous: true）
  // 自動モード（今まで通り）：話し終える（無音になる）と自動的に区切られる
  recognition.continuous = manualMode;
  recognition.interimResults = manualMode;
  recognition.lang = localeForLanguageName(sourceLanguageSelect.value);

  accumulatedText = "";
  isListening = true;
  micButton.classList.add("listening");
  waveform.classList.add("active");
  statusText.textContent = manualMode
    ? "聞いています...（もう一度マイクボタンを押すと終了します）"
    : "聞いています...";

  recognition.start();
}

async function startListeningWithAutoDetect() {
  // 「話す言語」「翻訳する言語」の2つを、判別の候補として扱う
  const codeA = localeForLanguageName(sourceLanguageSelect.value);
  const codeB = localeForLanguageName(targetLanguageSelect.value);

  try {
    detectionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showError("マイクを使用できませんでした。マイクの使用許可を確認してください。");
    return;
  }

  detectionChunks = [];
  detectionMediaRecorder = new MediaRecorder(detectionStream);
  detectionMediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) detectionChunks.push(event.data);
  };
  detectionMediaRecorder.onstop = async () => {
    detectionStream.getTracks().forEach((track) => track.stop());
    await sendAudioForDetection(new Blob(detectionChunks, { type: "audio/webm" }), [codeA, codeB]);
  };
  detectionMediaRecorder.start();

  // Web Speech API は「発話が終わったタイミング」の検出だけに使い、
  // その認識結果（テキスト）は使わない（言語判別の精度が低いため）
  const manualMode = manualModeCheckbox.checked;
  recognition.continuous = manualMode;
  recognition.interimResults = false;
  recognition.lang = codeA;

  isListening = true;
  micButton.classList.add("listening");
  waveform.classList.add("active");
  statusText.textContent = manualMode
    ? "聞いています...（もう一度マイクボタンを押すと終了します）"
    : "聞いています...（話す言語は自動で判別します）";

  recognition.start();
}

async function sendAudioForDetection(audioBlob, languageCodes) {
  statusText.textContent = "言語を判別しています...";
  const formData = new FormData();
  formData.append("audio", audioBlob, "speech.webm");
  formData.append("language_codes", languageCodes.join(","));

  try {
    const response = await fetch("/api/recognize-audio", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();

    if (!response.ok || data.error) {
      showError(data.error || "音声の判別に失敗しました");
      statusText.textContent = "マイクのボタンを押して話しかけてください";
      return;
    }
    if (!data.text) {
      statusText.textContent = "マイクのボタンを押して話しかけてください";
      return;
    }

    // 判別された言語が「話す言語」「翻訳する言語」のどちらに近いかを調べ、
    // 判別された方を実際の話す言語として、翻訳先を自動的に決める
    const detectedPrefix = (data.detected_language_code || "").split("-")[0].toLowerCase();
    const sourceCode = localeForLanguageName(sourceLanguageSelect.value).split("-")[0].toLowerCase();

    let actualSource = sourceLanguageSelect.value;
    let actualTarget = targetLanguageSelect.value;
    if (detectedPrefix && detectedPrefix !== sourceCode) {
      // 判別された言語が「翻訳する言語」側だった場合は、向きを入れ替える
      actualSource = targetLanguageSelect.value;
      actualTarget = sourceLanguageSelect.value;
    }

    statusText.textContent = `認識結果: ${data.text}`;
    await sendToServer(data.text, actualSource, actualTarget);
  } catch (err) {
    showError("サーバーとの通信に失敗しました。");
    statusText.textContent = "マイクのボタンを押して話しかけてください";
  }
}

function stopListeningUI() {
  isListening = false;
  micButton.classList.remove("listening");
  waveform.classList.remove("active");
}

if (recognition) {
  recognition.onresult = (event) => {
    if (autoDetectCheckbox.checked) return; // 自動判別モードでは、この結果は使わない

    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        accumulatedText += result[0].transcript;
      } else {
        interim += result[0].transcript;
      }
    }
    statusText.textContent = `認識結果: ${accumulatedText}${interim}`;
  };

  recognition.onerror = (event) => {
    if (!autoDetectCheckbox.checked) {
      statusText.textContent = `音声認識でエラーが発生しました（${event.error}）。もう一度お試しください。`;
    }
    stopListeningUI();
  };

  recognition.onend = async () => {
    stopListeningUI();

    if (autoDetectCheckbox.checked) {
      // 自動判別モードでは、録音の停止（→サーバーへの送信）はMediaRecorder側の処理に任せる
      if (detectionMediaRecorder && detectionMediaRecorder.state !== "inactive") {
        detectionMediaRecorder.stop();
      }
      return;
    }

    const finalText = accumulatedText.trim();
    accumulatedText = "";
    if (finalText) {
      await sendToServer(finalText);
    } else if (handsFreeCheckbox.checked) {
      // ハンズフリーモードでは、何も聞き取れなかった場合も自動的に聞き取りを再開する
      statusText.textContent = "ハンズフリーモード：聞いています...";
      setTimeout(() => {
        if (!isListening) startListening();
      }, 500);
    } else {
      statusText.textContent = "マイクのボタンを押して話しかけてください";
    }
  };
}

async function sendToServer(text, sourceLanguageOverride, targetLanguageOverride) {
  const sourceLanguage = sourceLanguageOverride || sourceLanguageSelect.value;
  const targetLanguage = targetLanguageOverride || targetLanguageSelect.value;

  statusText.textContent = "翻訳・音声生成中...";
  micButton.disabled = true; // 処理中は二重送信を防ぐため、マイクボタンを一時的に無効化する
  micButton.classList.add("processing");

  try {
    const response = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        source_language: sourceLanguage,
        target_language: targetLanguage,
        use_clone: useCloneCheckbox.checked,
        voice_tone: currentVoiceTone,
      }),
    });

    let data;
    try {
      data = await response.json();
    } catch (parseErr) {
      showError("サーバーからの応答が正しく読み取れませんでした。もう一度お試しください。");
      return;
    }

    if (!response.ok) {
      showError(data.error || "不明なエラーが発生しました");
      return;
    }

    addToTranscript(
      data.original_text, data.translated_text, data.original_audio_url,
      data.translated_audio_url, data.voice_cloned, data.id, data.timestamp
    );
    showLatestResult(
      data.original_text, data.translated_text, data.original_audio_url,
      data.translated_audio_url, data.voice_cloned, data.id, data.timestamp
    );
    scrollToTranscriptTop();

    player.src = data.translated_audio_url;
    try {
      await player.play();
    } catch (playErr) {
      statusText.textContent = "音声の再生に失敗しました。再生ボタンから再生してください。";
      if (handsFreeCheckbox.checked) {
        setTimeout(() => {
          if (!isListening) startListening();
        }, 500);
      }
      return;
    }

    statusText.textContent = "マイクのボタンを押して話しかけてください";

    // 会話モード：読み上げが終わったら、聞き取りを再開する。
    // 自動言語判別モードが有効な場合は、次にどちらの言語が話されても自動で判別されるため、
    // 言語の入れ替え（スワップ）は行わない。
    player.addEventListener("ended", () => {
      const conversationOn = conversationModeCheckbox.checked;
      const handsFreeOn = handsFreeCheckbox.checked;
      if (!conversationOn && !handsFreeOn) return;

      // 会話モードの時だけ、言語を入れ替える（ハンズフリーモード単独では、同じ方向で聞き取り続ける）
      if (conversationOn && !autoDetectCheckbox.checked) swapLanguages();

      statusText.textContent = conversationOn
        ? "会話モード：相手の返事を聞いています..."
        : "ハンズフリーモード：聞いています...";
      setTimeout(() => {
        if (!isListening) startListening();
      }, 300);
    }, { once: true });
  } catch (err) {
    showError("サーバーとの通信に失敗しました。サーバーが起動しているか確認してください。");
  } finally {
    micButton.disabled = false;
    micButton.classList.remove("processing");
  }
}

function showError(message) {
  statusText.textContent = "マイクのボタンを押して話しかけてください";
  const banner = document.createElement("div");
  banner.className = "error-banner";
  banner.textContent = message;
  document.querySelector(".stage").appendChild(banner);
  setTimeout(() => banner.remove(), 6000);

  // ハンズフリーモードでは、エラーが起きても画面操作なしで使い続けられるよう、
  // 少し待ってから自動的に聞き取りを再開する
  if (handsFreeCheckbox.checked) {
    setTimeout(() => {
      if (!isListening) startListening();
    }, 1500);
  }
}

function scrollToTranscriptTop() {
  latestResult.scrollIntoView({ behavior: "smooth", block: "start" });
}

function makePlayButton(audioUrl) {
  const button = document.createElement("button");
  button.className = "play-button";
  button.type = "button";
  button.setAttribute("aria-label", "再生する");

  const playIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`;
  const pauseIcon = `<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/></svg>`;
  button.innerHTML = playIcon;

  if (!audioUrl) {
    button.disabled = true;
    return button;
  }

  button.addEventListener("click", async () => {
    const isThisPlaying = player.src.endsWith(audioUrl) && !player.paused;
    if (isThisPlaying) {
      player.pause();
      return;
    }
    player.src = audioUrl;
    try {
      await player.play();
    } catch (err) {
      // 再生に失敗しても、画面全体は壊さない
    }
  });

  // 再生中はボタンのアイコンを一時停止マークに切り替える
  player.addEventListener("play", () => {
    button.innerHTML = player.src.endsWith(audioUrl) ? pauseIcon : playIcon;
  });
  player.addEventListener("pause", () => {
    if (player.src.endsWith(audioUrl)) button.innerHTML = playIcon;
  });
  player.addEventListener("ended", () => {
    if (player.src.endsWith(audioUrl)) button.innerHTML = playIcon;
  });

  return button;
}

function makeCopyButton(text) {
  const button = document.createElement("button");
  button.className = "copy-button";
  button.type = "button";
  button.setAttribute("aria-label", "コピーする");
  button.innerHTML = `
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="9" y="9" width="12" height="12" rx="2"/>
      <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>
    </svg>
  `;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      const original = button.innerHTML;
      button.innerHTML = `<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
      setTimeout(() => { button.innerHTML = original; }, 1500);
    } catch (err) {
      // コピーに失敗しても、画面全体は壊さない
    }
  });
  return button;
}

function makeFavoriteButton(text) {
  const button = document.createElement("button");
  button.className = "favorite-button";
  button.type = "button";
  button.setAttribute("aria-label", "よく使うフレーズに追加する");
  button.innerHTML = `
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
    </svg>
  `;
  button.addEventListener("click", async () => {
    await addCurrentTextToFavorites(text);
    button.innerHTML = `
      <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>
      </svg>
    `;
  });
  return button;
}

function buildBubblePair(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId, timestamp, options = {}) {
  const { withDelete = true } = options;
  const tag = voiceCloned
    ? '<span class="voice-tag voice-tag--cloned">あなたの声</span>'
    : '<span class="voice-tag voice-tag--natural">標準の声</span>';
  const timeLabel = timestamp ? `<span class="entry-timestamp">${formatTimestamp(timestamp)}</span>` : "";
  const pair = document.createElement("div");
  pair.className = "bubble-pair";
  pair.innerHTML = `
    <div class="bubble-row bubble-row--source">
      <div class="bubble bubble--source">${escapeHtml(original)}</div>
    </div>
    <div class="bubble-row bubble-row--translated">
      <div class="bubble bubble--translated">${escapeHtml(translated)}</div>
    </div>
    <div class="bubble-pair__footer">
      <span class="bubble-pair__footer-left">${tag}${timeLabel}</span>
    </div>
  `;

  pair.querySelector(".bubble-row--source").appendChild(makePlayButton(originalAudioUrl));
  pair.querySelector(".bubble-row--source").appendChild(makeFavoriteButton(original));

  const translatedRow = pair.querySelector(".bubble-row--translated");
  translatedRow.appendChild(makeCopyButton(translated));
  translatedRow.appendChild(makePlayButton(translatedAudioUrl));

  if (withDelete && entryId) {
    pair.querySelector(".bubble-pair__footer").appendChild(makeDeleteButton(entryId, pair));
  }

  return pair;
}

function addToTranscript(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId, timestamp) {
  const pair = buildBubblePair(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId, timestamp, { withDelete: true });
  transcript.prepend(pair);
  updateEmptyState();
}

// 直近のやり取りだけを、常に見える場所に表示する（履歴一覧を開かなくても最新の結果が分かるように）
function showLatestResult(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId, timestamp) {
  const pair = buildBubblePair(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId, timestamp, { withDelete: false });
  latestResult.innerHTML = "";
  latestResult.appendChild(pair);
  latestResult.style.display = "flex";
}

function formatTimestamp(timestamp) {
  // "2026-09-08 12:34:56"（サーバー側はUTC）を、見やすい表示に変換する
  try {
    const date = new Date(timestamp.replace(" ", "T") + "Z");
    return date.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch (err) {
    return "";
  }
}

function updateEmptyState() {
  const hasEntries = transcript.querySelector(".bubble-pair") !== null;
  let emptyState = transcript.parentElement.querySelector(".transcript-empty");
  if (hasEntries) {
    if (emptyState) emptyState.remove();
  } else {
    if (!emptyState) {
      emptyState = document.createElement("div");
      emptyState.className = "transcript-empty";
      emptyState.textContent = "まだ会話履歴がありません。マイクのボタンを押すか、下のテキスト欄に入力して話しかけてみましょう。";
      transcript.after(emptyState);
    }
    // 履歴パネルが閉じている間は、空状態メッセージも一緒に隠しておく
    emptyState.style.display = historyPanelOpen ? "" : "none";
  }
}

function makeDeleteButton(entryId, pairElement) {
  const button = document.createElement("button");
  button.className = "delete-entry-button";
  button.type = "button";
  button.textContent = "この履歴を削除";
  button.addEventListener("click", async () => {
    if (!confirm("この会話を履歴から削除しますか？")) return;
    try {
      const response = await fetch(`/api/history/${entryId}`, { method: "DELETE" });
      if (response.ok) {
        pairElement.remove();
        updateEmptyState();
      } else {
        statusText.textContent = "この履歴の削除に失敗しました。";
      }
    } catch (err) {
      statusText.textContent = "この履歴の削除に失敗しました。";
    }
  });
  return button;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// ---- テキスト入力での送信（音声が使えない・不安定な時の代わり） ----
textInputForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text) return;

  textInput.value = "";
  textInput.disabled = true;
  await sendToServer(text);
  textInput.disabled = false;
  textInput.focus();
});

micButton.addEventListener("click", () => {
  if (!isListening) {
    startListening();
  } else {
    // 手動モードでも自動モードでも、話している最中にもう一度押せば
    // そこで認識を終了できる（手動モードでは無音でも自動終了しないため、これが唯一の終了方法）
    recognition.stop();
  }
});

function swapLanguages() {
  const temp = sourceLanguageSelect.value;
  sourceLanguageSelect.value = targetLanguageSelect.value;
  targetLanguageSelect.value = temp;
}

swapButton.addEventListener("click", swapLanguages);

// ---- 会話履歴の読み込み・削除 ----
let historyLoaded = false;
let historyPanelOpen = false;

async function loadHistory() {
  try {
    const response = await fetch("/api/history");
    if (!response.ok) {
      updateEmptyState();
      return;
    }
    const history = await response.json();

    // 履歴は古い順に保存されているので、そのままの順で追加していくと
    // 最終的に一番新しいものが一番上に表示される（addToTranscriptはprependのため）
    history.forEach((entry) => {
      addToTranscript(
        entry.original_text,
        entry.translated_text,
        entry.original_audio_url,
        entry.translated_audio_url,
        entry.voice_cloned,
        entry.id,
        entry.timestamp
      );
    });
    updateEmptyState();
  } catch (err) {
    // 履歴が読み込めなくても、アプリ自体は使えるようにする
    updateEmptyState();
  }
}

// 「過去の履歴を見る」ボタン：押すたびに一覧の表示・非表示を切り替える。
// 履歴は毎回の会話ごとに画面下に増え続けると使いにくいため、普段は隠しておき、
// 見たい時だけこのボタンで呼び出せるようにしている。初めて開いた時に一度だけサーバーから読み込む。
toggleHistoryButton.addEventListener("click", async () => {
  historyPanelOpen = !historyPanelOpen;
  toggleHistoryButton.setAttribute("aria-expanded", String(historyPanelOpen));
  toggleHistoryButtonLabel.textContent = historyPanelOpen ? "履歴を閉じる" : "過去の履歴を見る";
  clearHistoryButton.style.display = historyPanelOpen ? "inline" : "none";

  if (historyPanelOpen && !historyLoaded) {
    historyLoaded = true;
    toggleHistoryButtonLabel.textContent = "読み込み中...";
    await loadHistory();
    toggleHistoryButtonLabel.textContent = "履歴を閉じる";
  }

  transcript.style.display = historyPanelOpen ? "flex" : "none";
  const emptyState = transcript.parentElement.querySelector(".transcript-empty");
  if (emptyState) emptyState.style.display = historyPanelOpen ? "" : "none";
});

clearHistoryButton.addEventListener("click", async () => {
  if (!confirm("会話履歴をすべて削除しますか？この操作は取り消せません。")) return;
  try {
    await fetch("/api/history", { method: "DELETE" });
    transcript.innerHTML = "";
    updateEmptyState();
  } catch (err) {
    statusText.textContent = "履歴の削除に失敗しました。";
  }
});

// ---- 声のサンプルを録音する ----
const RECORD_DURATION_MS = 60000; // 60秒間録音する（ElevenLabsの推奨：1〜2分程度の明瞭な音声）

let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;

recordSampleButton.addEventListener("click", async () => {
  if (isRecording) return;

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    recordStatusText.textContent = "このブラウザは録音に対応していません。";
    return;
  }

  recordSampleButton.disabled = true;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) recordedChunks.push(event.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      isRecording = false;
      recordSampleButton.classList.remove("recording");
      resetRecordButtonLabel();
      recordProgress.style.width = "0%";
      await uploadVoiceSample();
      recordSampleButton.disabled = false;
    };

    mediaRecorder.start();
    isRecording = true;
    recordSampleButton.classList.add("recording");

    const totalSeconds = RECORD_DURATION_MS / 1000;
    let secondsLeft = totalSeconds;
    recordButtonLabel.textContent = `録音中...（残り${secondsLeft}秒）`;
    recordStatusText.textContent = "はっきりとした声で、自然に話し続けてください。";

    const countdownTimer = setInterval(() => {
      secondsLeft -= 1;
      const elapsedRatio = (totalSeconds - secondsLeft) / totalSeconds;
      recordProgress.style.width = `${Math.min(elapsedRatio * 100, 100)}%`;
      if (secondsLeft > 0) {
        recordButtonLabel.textContent = `録音中...（残り${secondsLeft}秒）`;
      } else {
        clearInterval(countdownTimer);
      }
    }, 1000);

    setTimeout(() => {
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
      }
    }, RECORD_DURATION_MS);
  } catch (err) {
    recordStatusText.textContent = "マイクを使用できませんでした。マイクの使用許可を確認してください。";
    recordSampleButton.disabled = false;
  }
});

async function uploadVoiceSample() {
  recordStatusText.textContent = "声のサンプルを保存しています...";
  const blob = new Blob(recordedChunks, { type: "audio/webm" });
  const formData = new FormData();
  formData.append("audio", blob, "sample.webm");

  try {
    const response = await fetch("/api/save-voice-sample", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();

    if (!response.ok) {
      recordStatusText.textContent = `エラー: ${data.error || "保存に失敗しました"}`;
      return;
    }

    recordStatusText.textContent = "声のサンプルを保存しました。";
    voiceSampleStatus.textContent = "登録済み";
    voiceSampleStatus.classList.add("status-pill--ok");
    useCloneCheckbox.disabled = false;
    hasVoiceSample = true;
    recordSampleButton.classList.add("record-button--subtle");
    resetRecordButtonLabel();
  } catch (err) {
    recordStatusText.textContent = "サーバーとの通信に失敗しました。";
  }
}

// ============================================================
// ダークモード
// ============================================================
const THEME_STORAGE_KEY = "voicebridge_theme";

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  themeIconSun.hidden = theme === "dark";
  themeIconMoon.hidden = theme !== "dark";
}

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(saved || (prefersDark ? "dark" : "light"));
}

themeToggleButton.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  applyTheme(next);
  localStorage.setItem(THEME_STORAGE_KEY, next);
});

initTheme();

// ============================================================
// 言語ペアのクイック切り替え（よく使う組み合わせをブラウザに保存）
// ============================================================
const QUICK_PAIRS_STORAGE_KEY = "voicebridge_quick_pairs";
const MAX_QUICK_PAIRS = 6;

function getQuickPairs() {
  try {
    return JSON.parse(localStorage.getItem(QUICK_PAIRS_STORAGE_KEY)) || [];
  } catch (err) {
    return [];
  }
}

function saveQuickPairs(pairs) {
  localStorage.setItem(QUICK_PAIRS_STORAGE_KEY, JSON.stringify(pairs));
}

function renderQuickPairs() {
  const pairs = getQuickPairs();
  quickPairs.innerHTML = "";

  pairs.forEach((pair, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "quick-pair-chip";
    chip.innerHTML = `<span>${escapeHtml(pair.source)} → ${escapeHtml(pair.target)}</span>`;

    chip.addEventListener("click", () => {
      sourceLanguageSelect.value = pair.source;
      targetLanguageSelect.value = pair.target;
    });

    const removeBtn = document.createElement("span");
    removeBtn.className = "quick-pair-chip__remove";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      const updated = getQuickPairs().filter((_, i) => i !== index);
      saveQuickPairs(updated);
      renderQuickPairs();
    });
    chip.appendChild(removeBtn);

    quickPairs.appendChild(chip);
  });

  const addButton = document.createElement("button");
  addButton.type = "button";
  addButton.className = "quick-pair-chip quick-pair-chip--add";
  addButton.textContent = "+ 現在の組み合わせを保存";
  addButton.addEventListener("click", () => {
    const current = getQuickPairs();
    const source = sourceLanguageSelect.value;
    const target = targetLanguageSelect.value;
    if (current.some((p) => p.source === source && p.target === target)) return;
    if (current.length >= MAX_QUICK_PAIRS) current.shift();
    current.push({ source, target });
    saveQuickPairs(current);
    renderQuickPairs();
  });
  quickPairs.appendChild(addButton);
}

renderQuickPairs();

// ============================================================
// よく使うフレーズ（お気に入り）
// ============================================================
async function loadFavorites() {
  try {
    const response = await fetch("/api/favorites");
    if (!response.ok) return;
    const favorites = await response.json();
    renderFavorites(favorites);
  } catch (err) {
    // お気に入りが読み込めなくても、アプリ自体は使えるようにする
  }
}

function renderFavorites(favorites) {
  favoritesRow.innerHTML = "";
  if (!favorites.length) return;

  favorites.forEach((favorite) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "favorite-chip";
    chip.title = "タップして送信";
    chip.innerHTML = `<span>${escapeHtml(favorite.text)}</span>`;

    chip.addEventListener("click", async () => {
      chip.disabled = true;
      await sendToServer(favorite.text);
      chip.disabled = false;
    });

    const removeBtn = document.createElement("span");
    removeBtn.className = "favorite-chip__remove";
    removeBtn.textContent = "×";
    removeBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      try {
        await fetch(`/api/favorites/${favorite.id}`, { method: "DELETE" });
        loadFavorites();
      } catch (err) {
        // 削除に失敗しても、画面全体は壊さない
      }
    });
    chip.appendChild(removeBtn);

    favoritesRow.appendChild(chip);
  });
}

async function addCurrentTextToFavorites(text) {
  try {
    await fetch("/api/favorites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        source_language: sourceLanguageSelect.value,
        target_language: targetLanguageSelect.value,
      }),
    });
    loadFavorites();
  } catch (err) {
    // 失敗しても画面全体は壊さない
  }
}

loadFavorites();

// ============================================================
// 初回起動時の使い方ガイド（チュートリアル）
// ============================================================
const TUTORIAL_STORAGE_KEY = "voicebridge_tutorial_seen";

const TUTORIAL_STEPS = [
  {
    title: "ようこそ、ほんやくコンニャクへ",
    body: "あなたの声で、どんな言語の相手とも話せるようになる、リアルタイム音声翻訳アプリです。",
  },
  {
    title: "① まずは声を録音",
    body: "上の「声を録音する」ボタンから、あなたの声を60秒ほど録音すると、あなたの声のクローンで読み上げられるようになります（録音しなくても標準の声で使えます）。",
  },
  {
    title: "② 言語を選んでマイクをタップ",
    body: "話す言語・翻訳する言語を選んだら、中央の大きなマイクボタンを押して話しかけてください。",
  },
  {
    title: "③ 会話オプションを活用",
    body: "「会話モード」をオンにすると、読み上げ後に自動で聞き取りを再開できます。相手が話す言語が分からない時は「自動言語判別モード」も便利です。",
  },
];

function renderTutorial() {
  tutorialSteps.innerHTML = TUTORIAL_STEPS
    .map((step, index) => `
      <div class="tutorial-step" data-step="${index}" ${index === 0 ? "" : "hidden"}>
        <h3>${escapeHtml(step.title)}</h3>
        <p>${escapeHtml(step.body)}</p>
      </div>
    `)
    .join("");

  tutorialDots.innerHTML = TUTORIAL_STEPS
    .map((_, index) => `<span class="tutorial-dot${index === 0 ? " tutorial-dot--active" : ""}" data-dot="${index}"></span>`)
    .join("");
}

let tutorialCurrentStep = 0;

function showTutorialStep(index) {
  tutorialCurrentStep = index;
  tutorialSteps.querySelectorAll(".tutorial-step").forEach((el) => {
    el.hidden = Number(el.dataset.step) !== index;
  });
  tutorialDots.querySelectorAll(".tutorial-dot").forEach((el) => {
    el.classList.toggle("tutorial-dot--active", Number(el.dataset.dot) === index);
  });
  tutorialNext.textContent = index === TUTORIAL_STEPS.length - 1 ? "はじめる" : "次へ";
}

function openTutorial() {
  renderTutorial();
  showTutorialStep(0);
  tutorialOverlay.classList.add("is-visible");
}

function closeTutorial() {
  tutorialOverlay.classList.remove("is-visible");
  localStorage.setItem(TUTORIAL_STORAGE_KEY, "1");
}

tutorialNext.addEventListener("click", () => {
  if (tutorialCurrentStep < TUTORIAL_STEPS.length - 1) {
    showTutorialStep(tutorialCurrentStep + 1);
  } else {
    closeTutorial();
  }
});

tutorialSkip.addEventListener("click", closeTutorial);
helpButton.addEventListener("click", openTutorial);

if (!localStorage.getItem(TUTORIAL_STORAGE_KEY)) {
  openTutorial();
}

// ============================================================
// PWA（ホーム画面に追加してアプリのように使える機能）
// ============================================================
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // 登録に失敗しても、通常のWebアプリとしては引き続き使える
    });
  });
}

// ============================================================
// ハンズフリーモード（運転中・作業中など、画面操作なしで使うための設定）
// ============================================================
const HANDSFREE_STORAGE_KEY = "voicebridge_handsfree";

handsFreeCheckbox.addEventListener("change", () => {
  localStorage.setItem(HANDSFREE_STORAGE_KEY, handsFreeCheckbox.checked ? "1" : "0");
  if (handsFreeCheckbox.checked && !isListening) {
    startListening();
  }
});

if (localStorage.getItem(HANDSFREE_STORAGE_KEY) === "1") {
  handsFreeCheckbox.checked = true;
  // ページを開いた直後は、マイクの許可ダイアログなどとぶつからないよう少し待ってから開始する
  setTimeout(() => {
    if (!isListening) startListening();
  }, 800);
}

// ============================================================
// 読み上げの声色（トーン）
// ============================================================
const VOICE_TONE_STORAGE_KEY = "voicebridge_voice_tone";
let currentVoiceTone = localStorage.getItem(VOICE_TONE_STORAGE_KEY) || "standard";

function applyVoiceToneUI() {
  toneOptions.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tone === currentVoiceTone);
  });
}

toneOptions.forEach((button) => {
  button.addEventListener("click", () => {
    currentVoiceTone = button.dataset.tone;
    localStorage.setItem(VOICE_TONE_STORAGE_KEY, currentVoiceTone);
    applyVoiceToneUI();
  });
});

applyVoiceToneUI();
