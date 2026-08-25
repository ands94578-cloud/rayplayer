# rayplayer

把不同實驗室的模型放進同一個錄音間，讓它們自己聊。

市面上的 AI podcast 工具幾乎都是**一個模型分飾兩角**寫稿再丟 TTS。這個專案不是：
每個座位是一個真正不同的模型，用它自己的 API 講它自己的話。有趣的差異來自模型本身，
不是來自我們寫的人設。

目前是**純逐字稿**階段——先把對話做得值得聽，音訊只是包裝。

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

## panel.json

```jsonc
{
  "policy": "moderator",        // 或 "round-robin"
  "language": "zh-Hant",
  "max_words": 90,              // 每輪長度上限，可個別覆寫
  "host":     { "name": "Ray",    "provider": { "kind": "anthropic", "model": "...", "api_key_env": "ANTHROPIC_API_KEY" } },
  "speakers": [{ "name": "Claude", "provider": { ... }, "stance": "可選：指派立場" }]
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

**單一家掛掉不會中斷錄音。** 某個 provider 出錯就記下來跳過該輪；連續三次才停，
因為那代表設定有問題而不是請求有問題。

## 測試

```bash
python3 -m unittest discover -s tests
```

## 還沒做

TTS 與音檔合成、每位講者的音色配置、逐字稿轉節目的剪輯（拿掉開場白、合併過短的輪次）。
先讓逐字稿好看再說。
