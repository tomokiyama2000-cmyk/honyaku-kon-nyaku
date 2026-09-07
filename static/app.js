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
const recordSampleButton = document.getElementById("recordSampleButton");
const recordButtonLabel = document.getElementById("recordButtonLabel");
const recordStatusText = document.getElementById("recordStatusText");
const voiceSampleStatus = document.getElementById("voiceSampleStatus");
const textInputForm = document.getElementById("textInputForm");
const textInput = document.getElementById("textInput");
const clearHistoryButton = document.getElementById("clearHistoryButton");

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

function localeForLanguageName(name) {
  // サーバーから埋め込まれた対応言語表を使って、選ばれた言語名から
  // ブラウザの音声認識用ロケール（例: "ja-JP"）を調べる
  const info = window.LANGUAGE_TABLE[name];
  return info ? info.sr_code : "ja-JP";
}

function startListening() {
  if (!recognition || isListening) return;

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

function stopListeningUI() {
  isListening = false;
  micButton.classList.remove("listening");
  waveform.classList.remove("active");
}

if (recognition) {
  recognition.onresult = (event) => {
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
    statusText.textContent = `音声認識でエラーが発生しました（${event.error}）。もう一度お試しください。`;
    stopListeningUI();
  };

  recognition.onend = async () => {
    stopListeningUI();
    const finalText = accumulatedText.trim();
    accumulatedText = "";
    if (finalText) {
      await sendToServer(finalText);
    } else {
      statusText.textContent = "マイクのボタンを押して話しかけてください";
    }
  };
}

async function sendToServer(text) {
  statusText.textContent = "翻訳・音声生成中...";
  micButton.disabled = true; // 処理中は二重送信を防ぐため、マイクボタンを一時的に無効化する

  try {
    const response = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        source_language: sourceLanguageSelect.value,
        target_language: targetLanguageSelect.value,
        use_clone: useCloneCheckbox.checked,
      }),
    });

    let data;
    try {
      data = await response.json();
    } catch (parseErr) {
      statusText.textContent = "サーバーからの応答が正しく読み取れませんでした。もう一度お試しください。";
      return;
    }

    if (!response.ok) {
      statusText.textContent = `エラー: ${data.error || "不明なエラーが発生しました"}`;
      return;
    }

    addToTranscript(data.original_text, data.translated_text, data.original_audio_url, data.translated_audio_url, data.voice_cloned, data.id);

    player.src = data.translated_audio_url;
    try {
      await player.play();
    } catch (playErr) {
      statusText.textContent = "音声の再生に失敗しました。再生ボタンから再生してください。";
      return;
    }

    statusText.textContent = "マイクのボタンを押して話しかけてください";
  } catch (err) {
    statusText.textContent = "サーバーとの通信に失敗しました。サーバーが起動しているか確認してください。";
  } finally {
    micButton.disabled = false;
  }
}

function makePlayButton(audioUrl) {
  const button = document.createElement("button");
  button.className = "play-button";
  button.type = "button";
  button.setAttribute("aria-label", "再生する");
  button.innerHTML = `
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
      <path d="M8 5v14l11-7z"/>
    </svg>
  `;
  if (!audioUrl) {
    button.disabled = true;
    return button;
  }
  button.addEventListener("click", async () => {
    player.src = audioUrl;
    try {
      await player.play();
    } catch (err) {
      // 再生に失敗しても、画面全体は壊さない
    }
  });
  return button;
}

function addToTranscript(original, translated, originalAudioUrl, translatedAudioUrl, voiceCloned, entryId) {
  const tag = voiceCloned
    ? '<span class="voice-tag voice-tag--cloned">あなたの声</span>'
    : '<span class="voice-tag voice-tag--natural">標準の声</span>';
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
      ${tag}
    </div>
  `;

  pair.querySelector(".bubble-row--source").appendChild(makePlayButton(originalAudioUrl));
  pair.querySelector(".bubble-row--translated").appendChild(makePlayButton(translatedAudioUrl));

  if (entryId) {
    pair.querySelector(".bubble-pair__footer").appendChild(makeDeleteButton(entryId, pair));
  }

  transcript.prepend(pair);
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

swapButton.addEventListener("click", () => {
  const temp = sourceLanguageSelect.value;
  sourceLanguageSelect.value = targetLanguageSelect.value;
  targetLanguageSelect.value = temp;
});

// ---- 会話履歴の読み込み・削除 ----
async function loadHistory() {
  try {
    const response = await fetch("/api/history");
    if (!response.ok) return;
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
        entry.id
      );
    });
  } catch (err) {
    // 履歴が読み込めなくても、アプリ自体は使えるようにする
  }
}

clearHistoryButton.addEventListener("click", async () => {
  if (!confirm("会話履歴をすべて削除しますか？この操作は取り消せません。")) return;
  try {
    await fetch("/api/history", { method: "DELETE" });
    transcript.innerHTML = "";
  } catch (err) {
    statusText.textContent = "履歴の削除に失敗しました。";
  }
});

loadHistory();

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
      recordButtonLabel.textContent = "🎙️ 声を録音する（60秒）";
      await uploadVoiceSample();
      recordSampleButton.disabled = false;
    };

    mediaRecorder.start();
    isRecording = true;
    recordSampleButton.classList.add("recording");

    let secondsLeft = RECORD_DURATION_MS / 1000;
    recordButtonLabel.textContent = `録音中...（残り${secondsLeft}秒）`;
    recordStatusText.textContent = "はっきりとした声で、自然に話し続けてください。";

    const countdownTimer = setInterval(() => {
      secondsLeft -= 1;
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
    voiceSampleStatus.classList.add("voice-setup__status--ok");
    useCloneCheckbox.disabled = false;
  } catch (err) {
    recordStatusText.textContent = "サーバーとの通信に失敗しました。";
  }
}
