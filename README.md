# A-Multi-Agent-Framework-for-Translating-Natural-Language-to-Genetic-Circuits
本專案旨在降低合成生物學中基因電路設計的門檻。透過引入大型語言模型（LLMs），系統能將研究人員輸入的「自然語言描述」自動轉譯為 Cello 軟體可接受的硬體描述語言（Verilog）。 目前專案處於 MVP（最小可行性產品）階段，核心實作了多智能體對抗式設計 (Adversarial Multi-Agent Design) 機制，確保生成的基因電路邏輯嚴密且符合使用者預期。
開發方法聲明 (Development Methodology)
本專案的 MVP 原型採用了 AI 輔助編程工具（Cursor Vibe Coding）進行快速迭代開發。專案的核心學術與工程價值在於「系統工作流程架構設計」與「高約束性提示詞工程 (Prompt Engineering)」，基礎程式碼與介面橋接則藉助工具加速完成，以專注於驗證概念的可行性。


Core Features

三輪對抗式設計 (Agentic Debate Mechanism): 系統內建「設計師 (Designer)」與「審查員 (Critic)」兩個獨立的 LLM 角色。在不偏離使用者目標的前提下，雙方會進行 3 輪的閉環討論與挑錯，避免單一模型生成的邏輯盲區。
高約束性語法生成 (High-Constraint Generation): 透過獨立的總結模型 (Summarizer Agent) 統整辯論結果，並使用高約束的系統提示詞，精準輸出符合 Cello 規範的 Verilog 程式碼。
資安與成本控管 (BYOK 模式): 採用 Bring Your Own Key 模式，使用者需輸入自己的 API Key 執行運算，確保未經授權的用戶無法濫用配額，同時保障使用者的 prompt 資料隱私。


Workflow

目前的系統執行流程如下：

需求輸入: 使用者輸入自然語言描述（如：「當感測器 A 與 B 同時觸發時，輸出綠色螢光蛋白」）並提供 API Key。

智能體迭代協作: 「設計師」與「審查員」針對需求展開 3 輪自動交叉驗證。

收斂與總結: 第三方總結模型介入，統整出最佳邏輯方案。

人類在迴路中 (Human-in-the-Loop, HITL): 系統將暫停並向使用者展示總結方案。使用者可選擇「確認生成」或「提出修改意見」（若需修改，將帶入新條件重啟 3 輪辯論）。

Verilog 編譯: 根據最終確認的方案，嚴格產出 Cello 支援的 Verilog 程式碼。

手動橋接 (MVP 限制): 系統輸出 .v 檔案，使用者需手動下載並上傳至 Cello 網頁版進行後續模擬。


Roadmap

雖然目前的 MVP 已能驗證核心邏輯，但為了使本專案成為具備完整科學價值的工具，未來預計整合以下模組：

[安全] 生物安全審查機制 (Biosafety Review): 在最終程式碼生成前，加入生安審核閘門，確保設計的電路不具備潛在危險特徵（如毒素蛋白合成路徑）。
[資料] Cello 元件庫檢索 (RAG Integration): 導入向量資料庫，讓 LLM 了解 Cello 當下實際可用的基因元件（Parts/Gates），確保生成的邏輯閘在生物實體上是可被實作的。
[自動化] Cello API 串接: 取代現有的手動下載/上傳步驟，實現從自然語言輸入到 Cello 基因電路圖輸出的「端到端 (End-to-End)」全自動化。
[實驗] 實驗室自動化串接: 將生成的實驗設計與液體處理機器人（Liquid Handlers）等實驗室自動化設備結合。
