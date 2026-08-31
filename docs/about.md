# 問題背景、原型定位與研究動機 (Problem Context, Prototype Positioning, and Motivation)

本文件說明此原型試圖處理的問題、工作假設、目前可檢視的實作，以及適合由不同領域共同檢驗的開放問題。

This document explains the problem, working hypothesis, inspectable prototype,
and open questions that may benefit from review across several disciplines.

> **Archive status / 封存狀態（2026-08-30）：** 本專案已依有限結案契約
> `CLOSED_UNSUCCESSFUL_ARCHIVED`。最終完成的真實 provider-to-Cello
> full-path 專案數為 0；下文的功能與研究方向是原型內容及歷史探索脈絡，
> 不是仍在執行的產品 roadmap。詳見 [Archived Project Closeout](PROJECT_CLOSEOUT.md)。

---

## 1. 這是一個什麼專案？ (What is this project?)

這是一個探索「證據治理型 AI 輔助基因電路設計」的初步研究原型。它研究的不只是 AI 能否產生候選設計，而是每項可公開結論能否攜帶一個可由人員與機器共同檢查的「證據與權利包絡」：具名證據、來源與版本、生物情境、授權狀態、主張邊界，以及仍缺少的驗證。其價值應由問題定義、可檢視實作、現有證據與未解問題判斷，而非由個人作者敘事或功能數量判斷。

自然語言輸入、多智能體協作與 Cello 相容輸出是工作流的組成部分，但不是本專案唯一或主要的差異化主張。核心研究方向是：能否在候選產生之上加入可重現的證據治理層，使系統不只輸出設計，也能說明哪些主張受到支持、依據為何、使用權利是否明確，以及下一步需要取得什麼證據。

其核心功能是：
1. **自然語言翻譯**：將使用者輸入的自然語言調節邏輯意圖（例如：「僅在輸入 A 存在且輸入 B 不存在時激活 GFP」），翻譯為結構化的布林邏輯、真值表預期與相容於 Cello 的組合邏輯 Verilog 代碼。
2. **確定性與簡化模型評估**：區分外部或 mock Cello 結果，並以資源感知 ODE、擾動與其他計算檢查提供比較視角；這些結果不是對宿主細胞真實動態的校準預測。
3. **多智能體自我修復（Reflexion Loop）**：利用多個協同運作的 AI 智能體（PM, Builder, Translator, Critic, Consolidator）對設計方案進行自我審查與反覆迭代修復，抓出邏輯不匹配、動態邊際不足或序列特徵瑕疵等失敗模式。
4. **可追溯表示與交換**：生成 SBOL3 Turtle、GenBank、BOM 與組裝規劃表示，供後續檢視；檔案生成不代表生物有效性或第三方相容性認證。
5. **證據與主張治理**：以機器可讀的 Evidence Bill of Materials（E-BOM）連結公開主張與具名證據，記錄生物情境與授權狀態，並以確定性規則將主張判定為 `supported`、`limited`、`unsupported` 或 `blocked`。缺少實驗證據表示尚未受到支持，不表示實驗已經失敗。

> [!IMPORTANT]
> **生物學宣稱邊界 (Biological Claim Boundary)**：本專案提供的是**計算層面的設計輔助與候選篩選**，而非實驗驗證。高基準分數僅代表在目前簡化模擬假設下設計合理，並不保證活體內（in vivo）的真實可構建性與功能性。

---

## 2. 為什麼它會存在？ (Why does it exist?)

此 repository 起源於一個問題構想：當 AI 能生成流暢但未必可靠的生物設計時，能否建立一套工作流，讓系統主動暴露假設、證據缺口與下一步驗證需求？目前內容是該構想經 AI 輔助工程反覆實作與檢查後的初步原型，不宣稱個人獨立完成，也不宣稱已取得獨立科學驗證。

相較於主要處理邏輯合成的 Cello，以及以自然語言降低 Cello 使用門檻的 CELLM，本專案目前較可辯護的定位不是宣稱產生更好的生物電路，而是探索如何讓 AI 輸出的主張具有可追溯證據、明確權利狀態與可執行的限制條件。詳細且保守的比較方式見[競爭定位與比較範圍](competitive_landscape.md)。

### 問題動機與背景 (Problem Motivation)

本專案的核心存在動機可歸納為以下三點：

* **探索以環境反饋引導的智能體工作流 (Environment-Feedback Loop)**：
  傳統的大語言模型在處理複雜的生物設計任務時，常因長上下文漂移（Context Drift）而產生幻覺（Hallucination）。本專案旨在探索一種「閉環反饋」機制——AI 系統不單靠使用者指令工作，還能將真值表檢驗、Cello 編譯結果、ODE 模擬數值與基準分數等「環境反饋」作為結構化信號，引導智能體進行迭代與自我修正（Reflexion）。

* **解決 AI 生物學設計中的「偽流暢性」差距 (Addressing "Pseudofluency" Gap)**：
  大語言模型能生成看起來非常專業且令人信服的啟動子、邏輯閘或質體描述，但這些設計背後往往缺乏明確的約束與物理合理性。本專案選擇「不將 LLM 的直接輸出當作最終結果」，而是建立嚴格的翻譯、模擬、評估、批判鏈條，將設計背後的生物學假設與不確定性顯性化，使人類研究者能夠審查和糾錯。

* **尋求計算預估與生物學不確定性之間的平衡 (Balancing Computation and Biological Uncertainty)**：
  在物理實驗中，設計最終能通過測量來判定好壞。但在計算原型中，評估設計是否「優良」則複雜得多。邏輯一致性、動態模擬、代謝負載、拷貝數微擾、序列完整性都從不同維度影響著設計的潛在成功率。本專案花費了大量精力在評分與整備度（Readiness）層面上，就是希望系統能誠實呈現設計的優缺點與證據薄弱處，而非給出一個看似完美但無法運作的單一產物。

---

## 3. 目前原型包含什麼？ (What does the current prototype contain?)

目前 repository 包含一組可檢視的端到端計算路徑；下列項目是 preview implementation，不是學術或生物學完成度宣稱：
* **多智能體協同架構**：實作了包含專案經理（PM）、構建者（Builder）、翻譯者（Translator）、評論者（Critic）與整合者（Consolidator）的 Reflexion 迴圈，可完成從語意理解到 Verilog 生成與錯誤修復的完整流程。
* **Cello 映射與邊界處理**：整合了 Cello 映射包裝器（CelloWrapper），能區分「真實外部 Cello 對接」與用於流程測試的「模擬（Mock）Cello 映射」，明示元件分配的來源與不確定性。
* **資源感知 ODE 模擬器**：實作了簡化的 ODE 動態模擬，將游離核糖體與 RNAP 資源爭奪（Metabolic Burden）納入考量，並支持 C-terminal 活性降解標籤（LVA/LAV/ASV）與降解常數的耦合。
* **基準評估與 Readiness 分數系統**：設計了具版本的研究評分設定；固定 Case 01 公開基線使用 `research-v2-preview@1.9.0`，另保留 `research-v1.8@1.8.0` 作為 API 與歷史相容設定。評估維度涵蓋邏輯功能、動態邊際、robustness（Monte Carlo 對數常態複製數微擾）、代謝負載與序列品質等；並建立了 Readiness 等級（從概念到序列優化）。所有分數都只是計算排序訊號，不是實驗成功機率。
* **序列級品質控制與密碼子優化**：利用 Biopython 與 pydna 檢測同聚物、重複序列、限制酶切位點（如 Golden Gate 過渡位點與 Gibson 組裝 overlap $T_m$ 預估），並針對大腸桿菌（E. coli）進行同義密碼子優化（Codon Optimization）。
* **標準生物資訊格式匯出**：實作了 **SBOL 3 Turtle** 與 **GenBank (`.gb`)** 文件生成器，並以 repository 內的語法、解析與 round-trip regression checks 驗證目前契約。這不等同於已完成 SnapGene 或其他第三方工具的廣泛相容性認證。
* **互動式 Demo 介面**：開發了 Web 應用，讓研究人員能直觀檢視智能體對話、評分報告與模擬動態曲線。

---

## 4. 歷史與可選研究方向 (Historical and Optional Research Directions)

以下項目保留原專案曾考慮的研究方向，但不再代表已排程或獲授權的下一步。
任何重啟都需要新的研究問題、範圍、證據標準與執行契約。詳見
[future roadmap](future_roadmap.md)。

* **第一階段：基準校準與數據擬合**
  * 建立包含 20 個以上經典文獻（如 Cello 1.0 發表設計、Repressilator 等）的基準資料集，評估並消除評分系統的「偽陰性」（即因過於保守的懲罰項導致文獻中可行的設計被打低分）。
  * 引進 `lmfit` 程式庫，開發 API 與 UI 模組以匯入微孔板讀數儀（Plate Reader）的螢光/發光數據，自動擬合 Hill 方程式參數（$y_{\text{min}}, y_{\text{max}}, K_d, n$）並動態更新元件庫。
* **第二階段：生物物理與物理佈局建模**
  * 實作「佈局評論者（Layout Critic）」，分析基因在 DNA 鏈上的空間排列，防止因轉錄讀穿（Read-through）或啟動子干擾（Promoter Interference）導致設計失效。
  * 利用 `Tellurium` 與 `roadrunner` 進行更高效的時序輸入模擬，並結合 `PyDSTool` 進行分歧分析（Bifurcation Analysis），繪製出電路在不同宿主環境下的「安全運作窗口（Operating Window）」。
  * 引入 `BioCRNpyler` 以精確模擬外源電路與宿主基因組對核糖體的資源爭奪。
* **第三階段：智能體決策可解釋性**
  * 在 Web UI 中提供互動式診斷日誌，清晰指出評分偏低的生化或物理原因。
  * 基於生物物理約束優化 Reflexion 迴圈，當模擬不符預期時，引導 Builder 智能體進行具體的「自癒」（如更換降解標籤或轉錄因子）。
* **長期願景：支持基於 CRISPR (CRISPRi/CRISPRa) 的基因電路**
  * 突破蛋白質邏輯閘（Cello UCF）的數量限制與宿主特異性限制，轉向由序列編程的 CRISPR 系統。
  * 整合 `Cas-OffFinder` 進行宿主全基因組脫靶篩選，並利用 `NUPACK` 預測 gRNA 與標靶 DNA 的雜交自由能以預估其轉錄抑制/激活效率。

---

## 5. 合作與交流規劃 (Collaborations & Feedback)

### 適合的合作對象 (What kinds of collaborators would benefit this project the most?)

本專案期望能與以下領域的研究者進行學術交流與合作：
* **合成生物學 CAD 與自動化領域的研究者**：尋求開發基因編譯器、自動化 CAD 工具，或對 SBOL 3.0 標準感興趣的學者與工程師。
* **計算生物學/生物物理建模專家**：能對目前簡化的 ODE 模擬、資源負載模型、動態轉錄/翻譯速率，或未來規劃的分歧分析提供理論指導或數學修正的學術專家。
* **濕實驗室研究人員/實驗學家**：願意嘗試使用本系統進行早期候選方案篩選，並願意提供微孔板讀數儀（Plate Reader）數據以幫助校準與擬合 Hill 動力學參數的實驗團隊。
* **AI 與智能體工作流（AI4Science）研究者**：對複雜科學任務中的多智能體編排、反射式錯誤修復（Reflexion）、以及如何減少 LLM 幻覺感興趣的計算機科學研究人員。

### 目前最有價值的討論與回饋 (Valuable Discussions)

1. **AI 設計工具中「生物學不確定性」的呈現方式**：在使用者介面中，如何最安全、有效率地向非計算背景的生物學家展示模擬的局限性、缺失的元件證據以及潛在的失敗模式，避免使用者對 AI 產出的設計產生過度信任？
2. **ODE 模擬模型假設的合理性評審**：目前在 [model_assumptions.md](model_assumptions.md) 中列出的物理假設（如一階蛋白質成熟延遲、簡化的生長稀釋、與游離核糖體耦合等）是否存在顯著漏洞？應如何逐步引入更細緻的宿主代謝反饋？
3. **基準測試加權指標與驗證權重**：目前評估指標（[evaluation_metrics.md](evaluation_metrics.md)）的權重分配是否符合濕實驗室挑選設計時的直覺？
4. **物理佈局（Plasmid Layout Context Effect）的考量**：在進行自動化拼接時，如何用最精簡的啟發式規則或物理方程來捕捉鄰近轉錄單元之間的干擾（如轉錄讀穿、超螺旋干擾）？

### 優先聯繫的人幕類型 (Outreach Priorities)
* **合成生物學學術研究室主持人（PI）或博士後**：特別是研究方向涉及「基因電路編譯（Circuit Compilation）」、「基因設計自動化」或「無細胞系統/體外電路模擬」的實驗室。
* **SBOL 與 SynBioHub 社群的核心貢獻者**：能協助提升專案的互操作性，使其更契合國際學術標準。
* **生物人工智慧（AI4Science / AI for Biology）領域的科研人員**：聚焦於如何將機器學習與確定性科學模擬相結合的交叉學科研究者。

---

## 6. 目前暫不尋求的機會 (What we are NOT currently seeking)

為維持專案的研究聚焦度，以下類型的機會目前**不在**優先考慮範圍之內：
* **商業化融資或創業投資（Venture Funding）**：目前定位是問題導向、AI 輔助的研究原型，目標是取得方法、證據與邊界方面的回饋，暫不以商業推廣或產品化為主軸。
* **臨床、醫療或高規格法規驗證（Clinical/Regulatory Validation）**：系統尚未具備，亦無意圖在現階段解決複雜宿主相容性、生物安全法規、倫理審查或臨床安全等應用層面問題。
* **單純的高通量生物資訊管道（Bioinformatics Pipeline）優化**：本專案聚焦於「智能體編排與設計輔助工作流」，而非開發超大規模的高通量純序列分析或基因組組裝基礎設施。
* **無數據支持的直接濕實驗委託**：我們暫不尋求要求系統「保證設計成功率」的濕實驗室合作，除非該合作伴隨著共同開發、參數校準與數據回饋的學術共識。
