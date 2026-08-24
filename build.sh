#!/bin/sh
# 由 src/ 的三個片段組出兩份成品：
#   app.html   — Artifact 用（不含 <html>/<head>/<body> 外殼）
#   index.html — 可直接開啟 / 部署的完整網頁
set -e
cd "$(dirname "$0")"
cat src/app.head.html src/app.body.html src/app.js.html > app.html

{
  printf '%s\n' '<!doctype html>'
  printf '%s\n' '<html lang="zh-Hant">'
  printf '%s\n' '<head>'
  printf '%s\n' '<meta charset="utf-8">'
  printf '%s\n' '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
  printf '%s\n' '<meta name="theme-color" content="#0C1210">'
  printf '%s\n' '<meta name="apple-mobile-web-app-capable" content="yes">'
  printf '%s\n' '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
  printf '%s\n' '<meta name="description" content="百家樂路單記錄、下三路推導、八組策略即時投票與蒙地卡羅回測。手機優先。">'
  cat src/app.head.html
  printf '%s\n' '</head>'
  printf '%s\n' '<body>'
  cat src/app.body.html src/app.js.html
  printf '%s\n' '</body>'
  printf '%s\n' '</html>'
} > index.html

echo "built: app.html ($(wc -c < app.html) bytes), index.html ($(wc -c < index.html) bytes)"
