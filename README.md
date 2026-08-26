# rayplayer

把不同實驗室的模型放進同一個錄音間，讓它們自己聊。

市面上的 AI podcast 工具幾乎都是**一個模型分飾兩角**寫稿再丟 TTS。這個專案不是：
每個座位是一個真正不同的模型，用它自己的 API 講它自己的話。有趣的差異來自模型本身，
不是來自我們寫的人設。

逐字稿與音訊兩段是分開的：先把對話做得值得聽，再決定要不要花錢配音。

## 跑起來

零依賴，只要 python3。先離線試（不用任何 API key、不花錢，逐字稿是假的）：

```bash
python3 -m rayplayer --panel panel.json --topic "AI 生成的內容需不需要強制標示？" --turns 8 --offline
```

接真模型，把你有的 key 設好就行；沒設的那幾家會在該座位發言時報錯並跳過，不會中斷錄音：

```bash
export ANTHROPIC_API_KEY=...  OPENAI_API_KEY=...  GEMINI_API_KEY=...  XAI_API_KEY=...
python3 -m rayplayer --panel panel.json --topic "..." --turns 16 --policy moderator
```

輸出兩個檔到 `out/`：可讀的 `.md` 逐字稿，和含完整設定、每輪模型／延遲／token 的 `.json`
（要重跑、要算成本、要 diff 兩次錄音的差異都靠它）。

## 配音

錄完之後渲染音訊（`--audio` 錄完直接接；或事後對著 run record 單獨渲染）：

```bash
python3 -m rayplayer --panel panel.json --topic "..." --turns 12 --audio --jobs 4
python3 -m rayplayer.render --run out/<episode>.json --panel panel.json --jobs 4
```

`--offline-voices` 用單音代替語音：每個座位一個音高，不用 key、不花錢，
可以先確認輪次、間隔、拼接和時間軸都對再去燒 TTS 額度。

輸出是逐輪 `turn-003-Claude.wav` 加一個 `episode.wav`，外加 `cues.md`
時間軸（可以直接拿去當 show notes 的章節）。**重跑會沿用已存在的音檔**，
所以渲染到一半失敗、或只想重錄某一輪，都不用整集重付一次錢
（刪掉那一輪的 wav 再跑，或用 `--force` 全部重來）。

支援 `gemini`（預設，便宜、`style` 可以用自然語言下指令）和 `elevenlabs`
（音色一致性較好；`name` 填的是你 voice library 的 voice id，不是顯示名稱）。
兩邊都取 PCM 輸出，所以拼接只靠 stdlib 的 `wave`，**不需要 ffmpeg**。

## panel.json

```jsonc
{
  "policy": "moderator",        // 或 "round-robin"
  "language": "zh-Hant",
  "max_words": 90,              // 每輪長度上限，可個別覆寫
  "voice_defaults": { "kind": "gemini", "model": "...", "api_key_env": "GEMINI_API_KEY", "sample_rate": 24000 },
  "host":     { "name": "Ray",    "provider": { ... }, "voice": { "name": "Charon", "style": "以清楚的主持語氣說" } },
  "speakers": [{ "name": "Claude", "provider": { ... }, "voice": { "name": "Kore" }, "stance": "可選：指派立場" }]
}
```

`kind` 指的是**通訊協定不是公司**：`anthropic`、`gemini`、`openai`，以及 `openai-compatible`
——xAI、DeepSeek、Groq、OpenRouter、vLLM、Ollama 都走這個，改 `base_url` 就好。
`panel.json` 裡的 model id 請換成你的 key 實際能呼叫的。

## 幾個刻意的設計

**system prompt 刻意寫得很薄。** 節目的前提是差異來自模型本身，所以每多寫一行人設，
就毀掉一分訊號。留下的只有格式規則（只講自己的台詞、不要 markdown、長度上限），沒有個性。

**每個模型看到的歷史不一樣。** 自己講過的話以 `assistant` 身分回傳，別人的話以 `user` 身分
標上名字。模型因此對「我剛才承諾了什麼」有連續感，而不是讀一份第三人稱的會議紀錄。

**防互相附和。** 模型之間的 sycophancy 很強，三輪內容易全體同意。每輪用一個關鍵字啟發式
（中英文都認）偵測連續附和，連續 N 輪就在下一位的提示裡加一句：真的不同意就講，
真的同意就講點沒人講過的。這是啟發式不是裁判模型——誤判的代價只是多一行提示，
逐字稿裡會標記哪幾輪被推過（`nudged`）。

**輪次調度有兩種。** `round-robin` 是公平輪轉；`moderator` 讓主持人的模型看逐字稿決定下一棒，
但仍受公平性約束（不能連兩輪、發言數落後的優先），而且解析失敗就退回輪轉——
導演掛掉應該只值一個聳肩，不該毀掉整集錄音。

**聲音跟模型是分開設定的。** 沒有理由「坐著 GPT 的那個位子」一定要由 OpenAI 配音。
`voice` 獨立於 `provider`，換配音不用動模型，換模型不用重配音。

**配音是逐輪單講者，不是用多講者模式一次生一整段。** 逐字稿本來就是逐輪產生的，
逐輪配音才能把音色綁在座位上、單輪重錄、平行化，也不受各家多講者模式的人數上限限制。

**delivery 可以下指令，人設還是不給。** `style` 只影響語氣（「以清楚的主持語氣說」），
不進文字模型的 prompt。要區分四個聲音靠的是不同 voice，不是幫模型編個性。

**單一家掛掉不會中斷錄音。** 某個 provider 出錯就記下來跳過該輪；連續三次才停，
因為那代表設定有問題而不是請求有問題。

## 測試

```bash
python3 -m unittest discover -s tests
```

## 還沒做

逐字稿轉節目的剪輯（拿掉贅語、合併過短的輪次）、mp3／章節標記輸出、背景音。

## 兩個已知風險

- **繁中腔調**：Gemini 的「高品質評估語言」清單列的是簡體中文，台灣腔要先用
  `--audio` 生一小段實際試聽再決定，必要時換 Fish Audio 之類中文更強的。
- **四家的 adapter 都還沒對過真的 endpoint**（開發環境沒有任何 key），
  離線 mock 全綠不代表線上第一次就會通。
