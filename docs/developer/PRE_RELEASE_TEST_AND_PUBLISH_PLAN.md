# 發布前測試與推送計畫

> 狀態：Historical / superseded for the archived documentation release
> 適用版本：`0.x research preview`
> 建立日期：2026-07-25
> 目的：在不誇大科學證據、不混入私有或本機產物的前提下，把目前大型工作樹整理成可審查、可驗證、可回復的 GitHub 發布內容。

> **2026-08-30 封存註記：** 專案已 `CLOSED_UNSUCCESSFUL_ARCHIVED`。
> 本文件保留舊版大型軟體發布流程作為歷史紀錄；它不再授權產品實作、實驗執行
> 或一般 release。後續 GitHub 動作只限另行核准的 archived documentation
> allowlist，且 staging、commit、push 必須分別取得授權。

## 1. 結論與發布策略

目前工作樹橫跨 application、benchmark、resource model、EGMA、export、MCP、Web、文件及測試，不適合直接執行 `git add -A` 後一次推送。

本次採三段式發布：

1. **Draft PR gate**：整理範圍、排除私有與暫存產物、完成目標測試及基本品質檢查。
2. **Ready for Review gate**：完成全套回歸測試、人工 QA、claim audit、coverage baseline，以及高風險模組的 targeted mutation testing 或核准的書面例外。
3. **Merge / Release gate**：最新 PR head 的 CI 綠燈、PR 可合併、公開文件與實際證據一致，且沒有未處理的 P0/P1 缺陷。

在完成 Draft PR gate 前，發布判定為 **No-Go**。

## 2. 證據與主張邊界

所有測試與發布紀錄必須保留以下邊界：

- fixture、mock、synthetic、offline、local computational evidence 不等於 biological、wet-lab、external-tool 或 production validation。
- `Implemented preview` 不等於 `Integration accepted`、`Experiment accepted` 或 `Scientifically validated`。
- EGMA dry-run、synthetic sealed package、visible fixture 或 local replay 不得標示為正式 unseen holdout、confirmatory result 或 protocol freeze。
- EXP-011 的 preflight、calibration 或 offline replay 不得被用來宣稱 routed-model improvement。
- 一般發布前測試不得呼叫付費 provider。任何付費執行都需要另外指定正數 USD 上限並取得新的明確授權。
- 測試成功只證明被測的軟體契約；不證明可建造性、實驗效果、臨床安全性或生物結果。

## 3. 現有品質能力與缺口

| 能力 | 現況 | 本次定位 |
| --- | --- | --- |
| pytest 單元／整合／E2E 測試 | Implemented；知識圖譜辨識約 503 個 `test_*` 函式 | 必做 |
| Ruff | Implemented in CI | 必做 |
| Registry drift check | Implemented in CI | 必做 |
| `llms-full.txt` generated-file check | Implemented in CI | 必做 |
| 人工 Web QA 與 claim matrix | 公開程序見 `demo_checklist.md`；local-only `MVP_TEST_PLAN.md` 僅保留歷史執行紀錄 | 必做，且需針對目前 revision 重跑 |
| Coverage | CI 已執行 branch coverage 並上傳 JSON baseline；尚無數值門檻 | Implemented baseline；門檻仍待核准 |
| Gherkin／BDD | 無 `.feature`、Behave、Cucumber 或 pytest-bdd | 非本次硬門檻；可獨立試行 |
| Mutation testing | 已以 `mutmut` 對 `src/utils/safety_checker.py` 建立非 Windows targeted workflow | Implemented narrow gate；其他高風險模組仍待擴充 |
| 型別檢查 | `mypy` 已列入開發依賴；CI 對 pre-Cello 收口模組執行 scoped gate | Scoped gate；全庫 baseline 尚未清零 |

## 4. 角色與簽核

小型專案可由同一人兼任，但每個角色的判定仍需分開記錄。

| 角色 | 責任 |
| --- | --- |
| Release owner | 凍結範圍、維護 staging manifest、決定 Draft／Ready／Merge |
| Test owner | 執行自動測試、保存命令與結果、分類失敗 |
| QA reviewer | 執行人工 workflow、UI、failure-path 與 claim wording 檢查 |
| Scientific-claim reviewer | 確認 fixture／mock／offline／external／wet-lab 邊界 |
| Security/provenance reviewer | 檢查 secrets、私有檔案、第三方來源、授權與產物污染 |

禁止由「重跑後變綠」直接取代失敗分類或 reviewer 判定。

## 5. 工作項目總覽

### Phase 0 — 凍結範圍與建立 staging manifest

目標：先決定「這次要發布什麼」，再整理 Git。

- [ ] 記錄目前 branch、HEAD、remote-tracking ref 和工作樹狀態。
- [ ] 若允許網路操作，先 fetch，再以最新 `origin/main` 作為比較基準。
- [ ] 將所有 modified／untracked 路徑逐一標為：
  - `Commit 1..N`
  - `Keep local`
  - `Needs provenance review`
  - `Needs product decision`
- [ ] 禁止 `git add -A`。
- [ ] 確認所有既有 dirty changes 都被視為 user-owned，不 reset、clean、discard 或覆寫。
- [ ] 為每個 commit slice 列出對應的 production files、tests、docs 和 generated artifacts。
- [ ] 若兩個 slice 共享大型整合檔案，以可獨立通過測試的最小 hunk 分割；不可只按資料夾分割。

建議 commit slices：

1. 共用契約、schema、維護基礎與 duplication governance。
2. Resource competition／calibration M0–M6。
3. Export、adapter、MCP result access、workflow evidence 與 safety boundary。
4. EGMA offline research-preview contracts、generator、validation、feedback、sealing 與 fixtures。
5. Cross-cutting application/API/Web integration、公開文件與 generated artifacts。

**Exit criteria**

- 每個待提交檔案只有一個明確歸屬。
- 私有、暫存、輸出和實驗金鑰檔案不在 staging manifest。
- 每個 commit slice 有對應的 focused test 清單。

### Phase 1 — Repository hygiene 與公開範圍清理

- [ ] 將 `.codex_test_logs/` 納入忽略規則；不得提交其中的 verification logs、encrypted fixtures、unlock audit 或 pytest temp。
- [ ] 確認下列項目未被 staged：
  - `.pytest_cache/`
  - `.ruff_cache/`
  - `.llm_cache/`
  - `.ode_cache/`
  - `__pycache__/`
  - `tmp/`、`tmp_pytest/`、`pytest_temp*/`
  - `outputs/`
  - `venv/`、`.venv/`
  - `chroma_db/`
  - `local_plans_private/`
  - archives、binaries、database files、captured environments
- [ ] 檢查 line-ending-only diff；不要在功能發布中做全庫 LF/CRLF 正規化。
- [ ] 檢查大檔案、產生檔、重複 fixture 和非必要 snapshot。
- [ ] staged-scope secret scan：API keys、tokens、passwords、private URLs、外部 sealing keys、raw/hex/Base64/URL-safe Base64 key 表示。
- [ ] staged-scope provenance scan：外部複製程式碼、vendor tree、site-packages、node_modules、工具輸出、未知授權資料。
- [ ] 檢查第三方 dependency 與資料集授權。
- [ ] 保持 `primer3-py` 為選用 GPL dependency，不因測試方便加入核心 Apache-2.0 安裝。
- [ ] 驗證 Markdown 相對連結及 GitHub UTF-8 顯示；公開人工 QA 依 `demo_checklist.md`，local-only `MVP_TEST_PLAN.md` 不作為公開必要輸入。

**Exit criteria**

- secret findings：0。
- private/local artifact findings：0。
- 無未解釋的 binary、archive、vendor 或外部來源檔案。
- 所有 staged files 都在 manifest 內。

### Phase 2 — 靜態檢查、產生檔與基本契約

以 repository venv 執行：

```powershell
.\venv\Scripts\python.exe src\scripts\build_registry.py --check
.\venv\Scripts\python.exe src\scripts\generate_llms_txt.py
.\venv\Scripts\python.exe -m ruff check . --exclude venv,tmp_pytest,outputs,chroma_db,_docx_extract_tmp
.\venv\Scripts\python.exe scripts\verify_import_patches.py
git diff --check
```

執行 `generate_llms_txt.py` 後：

- [ ] 只接受 generator 產生的 `llms-full.txt` 變更。
- [ ] 人工檢查其差異與本次公開文件範圍一致。
- [ ] 完成 commit 後重跑 generator，必須不再產生 diff。

型別檢查先採 baseline 模式：

```powershell
.\venv\Scripts\python.exe -m mypy application benchmark_suite src
```

上述命令用於觀察全庫歷史 baseline，現階段不宣稱全綠。CI 的硬門檻只涵蓋本次 pre-Cello 收口模組：

```powershell
.\venv\Scripts\python.exe -m mypy `
  src\mcp_server\and2_pilot_preflight.py `
  src\schemas\and2_pilot.py `
  application\offline_contract_validation.py `
  src\catalog\agent_catalog.py
```

若現有 baseline 尚未清零：

- 不得把舊問題偽裝成新問題。
- 新增或修改模組不得增加未分類的 type errors。
- 將 mypy 升級為 CI hard gate 應使用獨立品質提交。

**Exit criteria**

- Registry check：PASS。
- Ruff violations：0。
- `git diff --check` findings：0。
- Generated-file drift：0。
- Import patch verification：PASS。

### Phase 3 — Focused unit 與 contract tests

每個 commit slice 在進入整合測試前先跑 focused tests。

#### 3A. 共用契約與維護基礎

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\test_boolean_coercion_contracts.py `
  tests\test_candidate_value_contracts.py `
  tests\test_optional_scalar_contracts.py `
  tests\test_score_clamp_contracts.py `
  tests\test_stable_json_hash_contracts.py `
  tests\test_legacy_json_listing_contracts.py `
  tests\test_repeated_test_factory_contracts.py `
  tests\test_duplicate_function_checker.py `
  tests\test_import_boundaries.py `
  tests\test_package_metadata_contract.py `
  -q --basetemp=tmp_pytest\pre_release_contracts
```

#### 3B. Resource competition／calibration

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\test_resource_calibration_m0.py `
  tests\test_resource_competition_m1.py `
  tests\test_resource_plate_reader_m2.py `
  tests\test_resource_parameter_fitting_m3.py `
  tests\test_resource_validation_m4.py `
  tests\test_resource_calibration_workflow_m5.py `
  tests\test_resource_model_analysis_m6.py `
  tests\test_plate_reader_calibration_exp022.py `
  tests\test_web_ode_trace_contract.py `
  tests\test_app_ode_charts.py `
  -q --basetemp=tmp_pytest\pre_release_resource
```

#### 3C. Export、adapter、MCP 與 safety

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\test_assembly_download_contract.py `
  tests\test_genbank_formatting_contract.py `
  tests\test_design_exporters.py `
  tests\test_phase9_adapter_conformance_matrix.py `
  tests\test_phase9_biological_tool_adapters.py `
  tests\test_mcp_result_access.py `
  tests\test_workflow_evidence_contracts.py `
  tests\test_safety_checker_phase8_lite.py `
  tests\test_safety_boundary.py `
  -q --basetemp=tmp_pytest\pre_release_safety
```

#### 3D. EGMA 與 EXP-011 防回歸

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\test_egma_benchmark_contract.py `
  tests\test_egma_feedback_contract.py `
  tests\test_egma_formal_audits.py `
  tests\test_egma_generator.py `
  tests\test_egma_sealed_package.py `
  tests\test_exp011_reproducibility.py `
  tests\test_model_routing.py `
  -q --basetemp=tmp_pytest\pre_release_egma
```

#### 3E. Publication 與跨層整合

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests\test_phase10_case_study_package.py `
  tests\test_phase10_11_publication_acceptance.py `
  tests\test_phase11_evidence_report.py `
  tests\test_scientific_data_integration.py `
  tests\test_v2_research_workspace.py `
  -q --basetemp=tmp_pytest\pre_release_publication
```

若清單中的測試檔案未被納入該 commit slice，仍應在最終完整回歸中執行。

**Exit criteria**

- Focused failures：0。
- Unexpected skips：0；所有 skip 必須有 dependency／平台／fixture 理由。
- Unexpected xpass：0。
- Flaky rerun 不可直接視為 PASS；必須記錄原始失敗、重現次數及處置。

### Phase 4 — 完整回歸與 CI parity

```powershell
.\venv\Scripts\python.exe -m pytest -q --basetemp=tmp_pytest\pre_release_full
```

若 Windows host policy、OneDrive 或 pytest plugin 造成環境性失敗：

1. 保存原始命令與完整錯誤。
2. 以 repository-local basetemp 重試。
3. 必要時使用乾淨環境或與 GitHub Actions 相同的 Python 3.12 Linux 環境重現。
4. 將 Application Control、DLL 或檔案權限問題標示為 host-policy evidence，不可直接標成產品缺陷。
5. 本機替代執行不能取代最新 head 的 GitHub Actions。

**Exit criteria**

- Full pytest failures：0。
- 最新整理後工作樹的完整結果已保存。
- 已知第三方 warnings 有來源與影響說明。
- 沒有只靠忽略／跳過測試取得的假性綠燈。

### Phase 5 — Coverage baseline 與品質指標

此能力已建立 **branch-coverage baseline**：CI 執行 `pytest-cov`、輸出終端缺漏資訊，並上傳 `outputs/pre_release/coverage.json`。目前沒有 `fail-under` 或 changed-lines 數值門檻，因此 baseline 不能被描述為 coverage gate。

建議命令：

```powershell
.\venv\Scripts\python.exe -m pytest `
  --cov=application `
  --cov=benchmark_suite `
  --cov=src `
  --cov-branch `
  --cov-report=term-missing `
  --cov-report=json:outputs\pre_release\coverage.json
```

目前只建立 baseline；reviewer 核准後再啟用數值門檻。

建議後續門檻：

- Changed executable lines coverage：至少 80%。
- Safety、claim boundary、hashing、validation、export blocker 等高風險決策模組：至少 90% branch coverage。
- 不允許新增未測的 fail-open 分支。
- Coverage 下降必須有原因、風險與補測計畫。
- Coverage 百分比不取代 scenario、property、mutation 或人工 QA。

**本次過渡規則**

- Draft PR 必須讓 CI coverage baseline 成功產出；若基礎設施故障，需留下可重播命令與書面限制。
- Ready for Review 前必須保存最新 head 的 baseline，或由 reviewer 明確接受「本次無 coverage 數字」的限制。

### Phase 6 — Targeted mutation testing

此能力已對 `src/utils/safety_checker.py` 建立 **targeted mutation workflow**，使用 `tests/test_safety_boundary.py`，且因工具相容性只在非 Windows runner 執行。不要求每次 push 執行全庫 mutation，也不把這個窄範圍結果外推成全庫 mutation 品質。

優先目標：

- `src/utils/safety_checker.py`
- `src/utils/boolean_values.py`
- `src/utils/scalar_values.py`
- `src/utils/hashing.py`
- `benchmark_suite/egma_contracts.py`
- `benchmark_suite/egma_validation.py`
- `benchmark_suite/egma_claim_audit.py`
- `benchmark_suite/egma_sealing.py`
- resource validation／promotion guards
- GenBank／assembly export blockers

後續擴充應維持獨立品質提交，逐一把其他純函式與 fail-closed decision logic 納入，並保存每個目標的 mutant 分類。

第一輪規則：

- 先建立 mutation baseline，不以任意全庫百分比否決 Draft PR。
- 每個 surviving mutant 必須分類：
  - missing test
  - equivalent mutant
  - unreachable/dead code
  - tool limitation
- Safety、claim boundary、sealing authentication、promotion guard 和 export blocker 不得有未審查的 surviving mutant。

建議穩定後門檻：

- Targeted mutation score：至少 80%。
- 高風險 fail-closed 分支：不得有可改變行為但仍存活的 mutant。
- Timeout／no-test mutants 必須被視為問題，不可計入 killed。

**本次發布規則**

- Draft PR：不以 mutation 阻擋。
- Ready for Review：若本次修改高風險純邏輯，需完成 targeted mutation，或留下 reviewer 核准的書面例外與替代 decision-table 測試證據。

### Phase 7 — Gherkin／BDD 決策

Gherkin 不是本次發布硬門檻。只有在下列條件成立時才導入：

- 非開發者 reviewer 需要直接審查 acceptance scenarios。
- 同一 user workflow 在文件、pytest 和人工 QA 間反覆產生語意歧義。
- Scenario 可維持穩定的業務語言，而不是封裝 Python implementation details。

若導入，使用獨立提交並先試行三個 scenario：

1. Incomplete sequence 阻擋 GenBank export。
2. External Cello unavailable 不得宣稱 real mapping success。
3. High-risk design request 在 save／run／export 維持 pause、block 與 audit trail。

試行驗收條件：

- `.feature` 與 step definitions 不複製大段既有 pytest。
- Scenario 能由產品／科學 reviewer 閱讀。
- CI 執行時間與維護成本可接受。
- BDD failure 能指出使用者契約，而非只有底層 exception。

未達條件時，保留現有 pytest scenario tests 與 claim matrix。

### Phase 8 — 人工 QA 與核心 workflow smoke

啟動主要 FastAPI／HTML 介面：

```powershell
.\venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

必測頁面／endpoint：

- `/web`
- `/web/runs`
- `/web/research`
- `/web/benchmarks`
- `/web/imports`
- `/web/assembly`
- `/web/designs`
- `/docs`
- `/api/v1/health`
- `/api/v2/health`

核心 workflow：

- [ ] Structured design intake、clarification 與 safe offline fallback。
- [ ] Run start、progress、pause、resume、cancel、retry、failed state。
- [ ] Candidate list、detail、compare、simulate、promote。
- [ ] ODE trace、temporal inputs、invalid inputs、truncation／warning metadata。
- [ ] Resource calibration create、list、detail、analysis 與 insufficient-data path。
- [ ] Benchmark／readiness report 的 fixture、provider、mapping、comparison eligibility 標示。
- [ ] Import JSON／GenBank、review、confirm、immutable revision 與 diff。
- [ ] Assembly／BOM／GenBank／SBOL3／PDF download 成功與 blocker paths。
- [ ] Share summary 不洩露 secrets、local paths 或 unsupported claims。
- [ ] Safety safe／warn／pause／block／audit-log paths。
- [ ] External tool unavailable、invalid ID、invalid JSON、missing artifact、path traversal。

UI 檢查：

- [ ] 1366×768 desktop。
- [ ] 窄 viewport。
- [ ] 中英文主要路徑。
- [ ] Navigation、form validation、loading、refresh、back/forward、downloads。
- [ ] 無 console JavaScript error、broken template 或 missing asset。
- [ ] 錯誤頁不顯示 API key、stack trace 或本機絕對路徑。
- [ ] Evidence boundary、warning、blocker 在關鍵操作附近可見。

Streamlit `app.py` 僅執行 legacy／maintenance smoke，不作為主要 MVP 介面驗收。

**Exit criteria**

- P0 workflows：100% PASS 或明確 No-Go。
- P1 failures：有 owner、影響、處置與是否接受為 limitation。
- P0/P1 未分類缺陷：0。

### Phase 9 — 科學證據、可重現性與公開文字稽核

- [ ] 所有 benchmark、calibration、EGMA、EXP-011、resource-model artifacts 有 input hash、config、seed、版本、provider/tool 狀態與產生時間。
- [ ] Derived observations 與 raw observations 分離。
- [ ] `not_run`、`unavailable`、`mock`、`fixture_only`、`not_mapped`、`not_comparable` 不被轉換成成功。
- [ ] `comparison_eligible=false` 不被 aggregate score 或 UI 隱藏。
- [ ] External adapter fallback 顯示實際執行的工具或 fallback。
- [ ] Stochastic／Monte Carlo 路徑保存 random seed 與 truncation metadata。
- [ ] 公開 docs、case studies、README、roadmap、model assumptions 與 UI wording 一致。
- [ ] 禁止以下主張，除非有外部相應證據：
  - wet-lab validated
  - biologically validated
  - guaranteed buildability
  - real Cello mapped
  - experimentally calibrated host model
  - formal confirmatory holdout
  - production ready

**Exit criteria**

- Claim mismatch findings：0。
- 每個公開 performance／readiness 數字都能追到對應 artifact。
- Local fixture evidence 未被包裝成外部驗證。

### Phase 10 — Evidence package 與發布紀錄

本機完整 logs 放在 ignored 位置：

```text
outputs/pre_release/<YYYY-MM-DD>_<commit-or-working-tree-id>/
  environment.md
  scope_manifest.md
  focused_tests/
  full_pytest.txt
  ruff.txt
  registry_check.txt
  generated_file_check.txt
  coverage/
  mutation/
  manual_qa.md
  security_provenance_scan.md
  claim_audit.md
  release_summary.md
```

公開 PR 只摘要：

- branch／head SHA
- commit slices
- 測試命令與通過數
- skips／warnings／known limitations
- coverage／mutation 狀態
- manual QA 範圍
- claim boundary
- provider calls與 paid cost
- Draft／Ready／Merge 判定

不得把包含 secrets、raw environments、private plans、付費憑證或未消毒原始資料的 evidence package 加入 Git。

### Phase 11 — Draft PR、Ready for Review 與 Merge

#### Draft PR gate

- [ ] Scope manifest 完成。
- [ ] 私有／本機 artifacts 為 0。
- [ ] Focused tests 通過。
- [ ] Ruff、registry、generated-file、import patch、diff check 通過。
- [ ] staged-scope secret/provenance scan 通過。
- [ ] PR title/body 與實際 scope 一致。
- [ ] 未完成的 coverage／mutation／manual QA 清楚列為 pending。

#### Ready for Review gate

- [ ] 最新 local full pytest 通過。
- [ ] 最新 PR head GitHub Actions 通過。
- [ ] Manual Web QA 完成。
- [ ] Claim audit 完成。
- [ ] Coverage baseline 已產出或書面接受限制。
- [ ] 高風險修改的 targeted mutation 已完成，或有核准例外。
- [ ] 沒有未處理 P0/P1 缺陷。
- [ ] PR body 已更新為實際最終 scope。

#### Merge / Release gate

- [ ] PR `mergeable=MERGEABLE` 且 merge state clean。
- [ ] 只採用最新 head SHA 的 CI 結果，不採用舊 run。
- [ ] `git diff --check origin/main...HEAD` 通過。
- [ ] Public-clean、claim、security/provenance findings 為 0。
- [ ] Reviewer 確認 docs、tests、artifacts、limitations 一致。
- [ ] 先更新 PR body，再轉 Ready for Review，最後才 squash merge。
- [ ] Merge 後確認 local main 與 origin/main 同步且工作樹狀態符合預期。

## 6. 品質指標儀表板

每次 release candidate 至少填寫：

| 指標 | Draft gate | Ready／Merge gate |
| --- | --- | --- |
| Focused test failures | 0 | 0 |
| Full pytest failures | 可 pending，但需揭露 | 0 |
| Unexpected skips／xpass | 0 | 0 |
| Ruff violations | 0 | 0 |
| Registry drift | 0 | 0 |
| Generated-file drift | 0 | 0 |
| `git diff --check` findings | 0 | 0 |
| Secret findings | 0 | 0 |
| Private/local artifact findings | 0 | 0 |
| Unreviewed P0/P1 defects | 0 | 0 |
| Claim mismatches | 0 | 0 |
| Coverage | baseline pending allowed | baseline／核准例外 |
| Targeted mutation | pending allowed | 高風險修改必須完成／核准例外 |
| Paid provider calls | 0 | 0，除非另有授權 |
| Wet-lab／biological evidence | 不宣稱 | 不宣稱，除非另有外部證據 |

## 7. 失敗分類與處置

每個失敗只能歸入下列一類：

1. **Product regression**：修正 code，增加 regression test，重跑 focused + full suite。
2. **Test defect／fixture drift**：修正測試契約或 fixture，說明為何不是降低門檻。
3. **Environment／host-policy**：保存環境證據，以 CI／乾淨環境重現。
4. **Optional dependency unavailable**：保留 missing-dependency path；不得強迫核心 CI 安裝不必要 dependency。
5. **Flaky／ordering／timing**：重現、隔離、建立 owner；不能只以 rerun green 結案。
6. **Generated artifact drift**：修正 generator 或重新產生；不可手改 generated output。
7. **Claim／documentation mismatch**：降低或修正文案；不能用較弱測試支持較強主張。
8. **Security／provenance finding**：立即阻擋發布，直到移除、替換或完成授權／來源說明。

## 8. Go／No-Go 決策格式

```text
Decision: GO | CONDITIONAL GO | NO-GO
Branch:
Head SHA:
Target:
Scope manifest:
Focused tests:
Full pytest:
Ruff:
Registry:
Generated files:
Coverage:
Mutation:
Manual QA:
Security/provenance:
Claim audit:
Provider calls:
Paid cost:
Known limitations:
Blocking findings:
Reviewer:
Date:
```

只有全部 Release gate 都有可驗證證據時才可標示 `GO`。`CONDITIONAL GO` 只適用於 Draft PR，不適用於 merge 或正式 release。

## 9. 建議執行順序

1. 建立精確 staging manifest。
2. 補上 `.codex_test_logs/` ignore 規則並確認不誤納其他檔案。
3. 依 commit slice 執行 focused tests。
4. 執行 Ruff、registry、generator、import patch 與 diff checks。
5. 執行 staged-scope secret／provenance／license scan。
6. 建立 Draft PR。
7. 在最新整理後 head 執行 full pytest。
8. 執行 manual Web QA 與 scientific claim audit。
9. 建立 coverage baseline。
10. 對高風險純邏輯執行 targeted mutation testing。
11. 更新 PR body，確認最新 head CI。
12. 通過 Ready gate 後轉 Ready for Review。
13. 通過 merge gate 後 squash merge。

## 10. 本文件本身的完成標準

本計畫是發布治理與測試規格，不代表其中工作已執行。每個 phase 的 checkbox、evidence path、head SHA 與 reviewer 判定必須在實際 release candidate 上重新填寫；舊 revision 的 PASS 不得沿用為目前 revision 的 PASS。
