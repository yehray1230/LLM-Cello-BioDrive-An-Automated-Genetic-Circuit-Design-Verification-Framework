# 真實測試、截圖與螢幕錄影證據採集指南

> **文件狀態：Archived hypothetical protocol。** 專案已於 2026-08-30
> `CLOSED_UNSUCCESSFUL_ARCHIVED`，且沒有完成真實 provider-to-Cello full-path
> 專案。本文件保留的是未來執行時可採用的證據採集模板，不代表外部 Cello、
> Gemini 或完整 UI 流程曾依此完成。任何文中的「completed」畫面或公開敘述都
> 是條件式範例，不是目前專案證據。

本指南用於建立可放在 README、專案網站、作品集或研究預覽頁面的公開證據。目標不是只展示漂亮圖片，而是讓讀者能沿著同一個 run ID，從使用者操作一路追查到 Cello、ODE、評分與原始 artifact。

## 1. 證據層級

建議同時保留四種證據：

1. **使用者操作證據**：真實 UI 截圖或未剪接螢幕錄影。
2. **系統結果證據**：Run Monitor、候選頁面、評分圖與 ODE 圖。
3. **外部工具證據**：Cello technology mapping、DNAplotlib、stdout/stderr 與 artifact manifest。
4. **可稽核資料**：`summary.json`、`best_topology.json`、Verilog、run ID、版本與檔案雜湊。

UI 截圖證明使用者能走完產品流程；原始 artifact 證明畫面不是手工拼出的結果。兩者不可互相取代。

## 2. 建議使用的固定展示案例

為了讓不同日期的證據可比較，公開展示優先使用以下 AND 案例：

```text
Design an Escherichia coli genetic circuit that produces GFP only when both inputs A and B are present. Truth table: 00=OFF, 01=OFF, 10=OFF, 11=ON. Prefer a Cello-compatible two-input AND implementation.
```

錄影時也可顯示中文說明，但送入系統的文字應完整保留，避免每次改寫造成結果不可比較。

預期邏輯：

```text
Y = A AND B
00 -> OFF
01 -> OFF
10 -> OFF
11 -> ON
```

## 3. 測試前檢查

錄影或截圖前，請先完成以下檢查，但不要在畫面上顯示完整 API Key：

- Git revision 或 commit ID 已記錄。
- 系統日期、時區與模型名稱已記錄。
- Gemini provider 可連線；API Key 僅顯示「已設定」或遮蔽值。
- Podman machine 可用。
- 使用的 Cello image ID 已記錄。
- UCF、input sensor、output device 檔名已記錄。
- Cello command 使用 `{candidate_filename}`，不能固定為 `candidate_0.v`。
- `enable_ode = true`。
- 確認不是 Demo mode 或 mock Cello。
- 關閉通知、聊天視窗及任何可能洩漏個資的畫面。

推薦記錄欄位：

```text
Date/time:
Timezone:
Git revision:
Provider/model:
Cello image ID:
UCF:
Input sensor file:
Output device file:
Run ID:
```

## 4. 完整螢幕錄影腳本

建議錄製 1080p、30 fps，保留游標，不加生成式背景或模擬動畫。完整流程約需 4–8 分鐘；Cello 執行期間可加速播放，但應保留原始未剪接版本。

### 開場：證明是真實系統

1. 顯示專案首頁或 Run 列表。
2. 顯示目前日期與專案版本，但不要顯示敏感路徑或帳號資訊。
3. 開啟設定頁面。
4. 顯示 provider、model，以及 API Key 已設定的遮蔽狀態。
5. 顯示 Cello 為 external mode 所需的設定狀態。

### 輸入與啟動

1. 進入「新增設計」。
2. 貼上本指南的固定 AND prompt。
3. 讓觀眾看清楚 host、compute budget、ODE 與其他選項。
4. 啟動 run。
5. 立即記錄畫面上的 run ID。

### 執行中

至少讓以下階段各出現在畫面一次：

- Builder
- Translator
- Cello
- Data mining（若啟用）
- ODE simulation
- Critic
- Consolidator

不要只剪出成功的最後一秒。保留排隊、進度更新和可能的候選失敗，反而更能證明系統沒有隱藏錯誤。

### 完成畫面

完成後停留 5–10 秒，確保以下欄位可讀：

- run ID
- `completed`
- `approved`
- `mapping_status = mapped`
- `cello_mode = external`
- `cello_claim_level = externally_mapped`
- raw Cello assignment score
- normalized assignment score
- weighted total score 與 grade
- `ode_status = simulated`

接著依序打開：

1. 最佳候選或 candidate workbench。
2. Verilog 與真值表。
3. score breakdown。
4. ODE 圖。
5. Cello technology mapping 或 DNAplotlib 圖。
6. artifact manifest 或下載／輸出清單。

## 5. 必截圖片清單

| 編號 | 建議畫面 | 必須看見 | 支援的宣稱 |
| --- | --- | --- | --- |
| 01 | Run Monitor 完成頁 | run ID、completed、approved、100% | 真實使用者流程完成 |
| 02 | 最佳候選摘要 | mapped、external、raw score、總分 | 成功 external Cello mapping |
| 03 | Verilog／真值表 | AND 邏輯與四列輸出 | 語意與邏輯一致 |
| 04 | Cello technology mapping | biological gate assignments | Cello computational mapping 產物存在 |
| 05 | DNAplotlib／layout | mapped construct layout | Cello 預測設計 artifact 存在 |
| 06 | Score breakdown | component scores、總分、grade | 多維度計算評估完成 |
| 07 | ODE summary | time axis、output trace、run ID 或圖說 | 簡化動態模擬完成 |
| 08 | Artifact manifest | exit code、檔案數、netlist、image/command | 原始輸出可稽核 |

每張圖只負責一個主要訊息。不要把所有內容縮在同一張無法閱讀的全頁截圖中。

## 6. 截圖原則

- 優先使用您親自在真實 UI 中截取的畫面。
- 不用生成式圖片重畫數據圖、介面或 Cello mapping。
- 可以裁切空白區，但不要裁掉 run ID、狀態或圖例。
- 不要用後製改動數值、狀態、座標軸或警告。
- 若加箭頭或框線，保留一份無標註原圖。
- 每張公開圖片應有圖說，說明來源 run ID 與限制。
- API Key、使用者名稱、Email、本機絕對路徑與存取 token 必須遮蔽。

推薦圖說：

```text
Run <run_id>. Real Gemini API and external Cello computational mapping using the recorded UCF. The ODE and benchmark values are computational screening results, not wet-lab validation.
```

## 7. 檔名與資料夾結構

```text
evidence/<YYYY-MM-DD>_<run-id>/
  00_metadata.md
  01_run_monitor.png
  02_best_candidate.png
  03_truth_table_verilog.png
  04_cello_technology_mapping.png
  05_cello_dnaplotlib.png
  06_score_breakdown.png
  07_ode_summary.png
  08_artifact_manifest.png
  recording_full.mp4
  recording_public_edit.mp4
  summary.json
  best_topology.json
  artifact_manifest.json
  best_design.v
  SHA256SUMS.txt
```

`recording_full.mp4` 應保留原始未剪接版本；`recording_public_edit.mp4` 可加入章節、字幕與等待階段加速。

## 8. `00_metadata.md` 範本

```markdown
# Evidence metadata

- Capture date:
- Timezone:
- Run ID:
- Git revision:
- Provider/model:
- Prompt:
- Host organism:
- Cello mode:
- Cello image ID:
- UCF:
- Input sensor file:
- Output device file:
- Cello exit code:
- Mapping status:
- Raw assignment score:
- Normalized assignment score:
- Weighted total score:
- Grade:
- ODE status:
- Approved:
- Known warnings:

## Claim boundary

This packet is computational evidence. It is not wet-lab validation, fabrication readiness, or evidence of in vivo function.
```

## 9. 公開影片建議剪輯

公開版可控制在 90–180 秒：

1. 0–10 秒：問題與固定 prompt。
2. 10–30 秒：在真實 UI 輸入並啟動。
3. 30–60 秒：加速展示 Builder、Translator、Cello、ODE、Critic。
4. 60–90 秒：完成狀態與最佳候選。
5. 90–130 秒：Cello mapping、評分圖與 ODE 圖。
6. 最後 10 秒：run ID、artifact 連結與 claim boundary。

請勿用剪輯讓失敗候選看起來像不存在。可以說明系統嘗試多個候選，最後只核准符合門檻者。

## 10. 發布前 GO／NO-GO

只有全部符合時才使用「real external Cello mapping」：

- `cello_mode = external`
- `cello_claim_level = externally_mapped`
- `mapping_status = mapped`
- `cello_buildable = true`
- Cello return code 為 `0`
- artifact manifest 存在
- raw assignment score 可由 stdout 或 artifact 重現

以下任一情況應標示失敗或不發布成功宣稱：

- `mock_only`
- `unmapped`
- `MAPPING_FAILED`
- `external_mapping_failed`
- 缺少 UCF／sensor／device provenance
- 只有 UI 數字，沒有 manifest 或原始 artifact

## 11. 宣稱邊界

可以說：

> The system completed a real Gemini-assisted computational design run, external Cello mapping, ODE screening, and evidence-aware scoring for the recorded input and configuration.

不可以說：

- 已經 wet-lab validated。
- 已證明會在細胞內正常運作。
- 可直接製造或 fabrication-ready。
- normalized score 是成功機率。
- 單次成功代表所有輸入與環境都可靠。

## 12. 本專案目前的參考成功 run

目前可用來核對流程與欄位的參考 run 是 `run_74f338895932`：

- status：`completed`
- approved：`true`
- mapping：`mapped`
- Cello mode：`external`
- raw assignment score：`127.23`
- normalized assignment score：`1.0`
- weighted total score：`0.9269897502`
- grade：`Excellent`
- ODE：`simulated`

這個 run 可作為格式範例；若要公開成「您親自操作的證據」，仍建議依本指南重新錄製可見 UI 流程，並保留新的 run ID 與原始檔案。
