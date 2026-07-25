# Model Assumptions/模型假設

This document explains the assumptions behind the current resource-aware ODE simulation model. It is intended to make the model's scope clear for synthetic biologists, reviewers, and future contributors.

本文件解釋了目前資源感知 ODE 模擬模型背後的假設。其目的是向合成生物學家、審查者與未來的貢獻者明確說明該模型的範圍。

The simulator is designed for early computational triage. It helps compare candidate regulatory-circuit topologies, detect obvious dynamic failures, and provide signals for the Reflexion loop. It is not intended to be a full host-cell model or a quantitative prediction of in vivo expression.

模擬器旨在進行早期的計算篩選。它有助於比較候選調節電路的拓撲結構、檢測明顯的動態失效，並為 Reflexion 迴圈提供訊號。它並非旨在作為一個完整的宿主細胞模型，或對活體內（in vivo）表達進行定量預測。

The terminology and boundaries in this document are informed by published work
on Cello design automation, stochastic chemical simulation, resource
competition, and SBOL data exchange [1-4]. The equations, parameter defaults,
score weights, and readiness labels in this repository are nevertheless
project-specific implementations unless a section explicitly states otherwise.
They have not been calibrated against a wet-lab dataset.

本文件的術語與邊界參考 Cello 設計自動化、隨機化學模擬、資源競爭與 SBOL
資料交換的公開文獻 [1-4]。然而，除非段落另有明示，本專案中的方程式實作、
預設參數、評分權重與 readiness 標籤均為專案特定設計，尚未以濕實驗資料集校準。

## 1. Modeling Purpose/建模目的

The model is used to answer limited design-screening questions:

該模型用於回答有限的設計篩選問題：

- Does a candidate produce a stable output under the implemented dynamics?
  在實現的動力學下，候選方案是否能產生穩定的輸出？
- Does the simulated output show a useful dynamic margin?
  模擬的輸出是否顯示出有用的動態邊際？
- Does the candidate appear sensitive to kinetic-parameter perturbations?
  該候選方案是否對動力學參數的微擾敏感？
- Does the candidate impose high coarse-grained RNAP/ribosome demand?
  該候選方案是否施加了高粗粒度的 RNAP/核糖體需求？
- Should the Critic route the candidate toward repair, rejection, or consolidation?
  Critic 應該將候選方案引導至修復、拒絕還是鞏固？

The model should not be used to claim that a candidate will work experimentally. It provides relative evidence for ranking candidates generated under similar assumptions.

該模型不應被用來宣稱候選方案在實驗上一定可行。它僅為在相似假設下生成的候選方案提供用於排序的相對證據。

## 2. System Boundary/系統邊界

The current model represents a regulatory-circuit topology at a coarse level. It includes:

當前的模型在粗粒度水平上表徵調節電路拓撲。它包括：

- mRNA dynamics for inferred genes or gates;
  推導出的基因或邏輯閘之 mRNA 動力學；
- protein dynamics for inferred genes or gates;
  推導出的基因或邏輯閘之蛋白質動力學；
- shared RNAP availability;
  共享的 RNAP 可用性；
- shared ribosome availability;
  共享的核糖體可用性；
- aggregate burden proxy from total mRNA and protein;
  來自總 mRNA 和蛋白質的聚合負載代理指標；
- optional Monte Carlo perturbation of selected kinetic parameters.
  對選定動力學參數進行可選的蒙特卡羅微擾。

It does not represent a complete plasmid or full cellular physiology. In particular, it does not specify or simulate plasmid backbone, origin of replication, selectable marker, full DNA sequence, assembly strategy, host growth, or host-wide metabolism.

它並不代表完整的質體或完整的細胞生理學。特別是，它不指定或模擬質體骨架、複製起點、篩選標記、完整 DNA 序列、組裝策略、宿主生長或宿主全局代謝。

The current ODE explanation layer reports what can be read from the stored trajectory, such as peak output, time to peak, final output, coarse burden proxies, resource occupancy, and whether the final segment appears near steady state. These are derived readouts from the existing trace, not additional biological mechanisms.

目前的 ODE 解釋層會回報可從已保存軌跡讀出的內容，例如最大輸出、達峰時間、最終輸出、粗略負載代理指標、資源佔用率，以及最後一段軌跡是否看似接近穩態。這些是從既有軌跡衍生出的讀數，而不是額外的生物學機制。

## 3. State Variables/狀態變數

For `n` inferred genes or gates, the ODE state is a $3n$-dimensional vector:

對於 `n` 個推導出的基因或邏輯閘，ODE 狀態為一個 $3n$ 維向量：

```text
y = [mRNA_1 ... mRNA_n, P_immature_1 ... P_immature_n, P_mature_1 ... P_mature_n]
```

Where:
- `mRNA_i` is the mRNA concentration of gene `i` (nM)
- `P_immature_i` is the immature/unfolded protein concentration of gene `i` (nM)
- `P_mature_i` is the mature/folded active protein concentration of gene `i` (nM)

其中：
- `mRNA_i` 為基因 `i` 的 mRNA 濃度 (nM)
- `P_immature_i` 為基因 `i` 的未成熟/未折疊蛋白質濃度 (nM)
- `P_mature_i` 為基因 `i` 的已成熟/已折疊活性蛋白質濃度 (nM)

The simulator also records resource-related values over time:

模擬器還會記錄隨時間變化的資源相關數值：

```text
rnap_free
ribosome_free
rnap_occupancy
ribosome_occupancy
burden_nM
```

`burden_nM` is a coarse aggregate of all synthetic species:

`burden_nM` 是一個包含所有合成物種的粗略聚合值：

```text
burden_nM = sum(mRNA) + sum(P_immature) + sum(P_mature)
```

This is a proxy used for screening, not a measured cellular burden model.

這是一個用於篩選的代理指標，而非經測量的細胞負載模型。

## 4. Gene Count and Topology Approximation/基因數量與拓撲近似

The simulator infers `gene_count` from the topology. If `gate_count` is available, it uses that value. Otherwise, it estimates count from Verilog primitives and `assign` statements.

模擬器從拓撲結構中推導 `gene_count`。如果 `gate_count` 可用，它將使用該值。否則，它將從 Verilog 原語（Primitives）與 `assign` 語句中估算數量。

This means the ODE model treats a logic-level topology as a simplified chain of expression units. It does not infer a complete biological implementation from sequence or part-level design.

這意味著 ODE 模型將邏輯層級的拓撲結構視為表達單位的簡化鏈。它不從序列或元件層級設計中推導出完整的生物學實現。

## 5. Regulatory Assumptions/調節假設

The current regulation model is Hill-like repression. The first species is treated as unregulated, and downstream species can be repressed by upstream protein levels:

目前的調節模型是類希爾（Hill-like）抑制。第一個物種被視為不受調節的，下游物種可被上游蛋白質水平所抑制：

```text
regulation[0] = 1.0
regulation[i] =
  leak + (1.0 - leak) / (1.0 + (protein[i - 1] / kd) ^ hill)
```

This is a coarse topology-level approximation. It does not model promoter-specific binding kinetics, multiple operator sites, activator mechanisms, cooperative binding variants, inducer dynamics, or detailed transcription-factor biophysics.

這是一個粗略的拓撲級近似。它不模擬啟動子特異性結合動力學、多個算子位點、激活物機制、協同結合變體、誘導物動力學或詳細的轉錄因子生物物理學。

## 6. Resource Assumptions/資源假設

The model treats RNAP and ribosomes as shared resource pools. Transcription depends on available RNAP, and translation depends on available ribosomes:

該模型將 RNAP 和核糖體視為共享資源池。轉錄取決於可用的 RNAP，而翻譯取決於可用的核糖體：

```text
rnap_factor = rnap_free / (km_rnap + rnap_free)
ribosome_factor = ribosome_free / (km_ribosome + ribosome_free)
```

The ODE dynamics are:

ODE 動力學為：

```text
d_mRNA_i/dt =
  transcription_rate * copy_number * regulation_i * rnap_factor
  - (mrna_degradation_rate + mu) * mRNA_i
```

```text
d_P_immature_i/dt =
  translation_rate * mRNA_i * ribosome_factor
  - (protein_degradation_rate + maturation_rate + mu) * P_immature_i
```

```text
d_P_mature_i/dt =
  maturation_rate * P_immature_i
  - (protein_degradation_rate + mu) * P_mature_i
```

Where:
- `copy_number` ($C$) scales the plasmid-borne promoter resource demand and transcription rates.
- `mu` ($\mu(t)$) is the dynamic growth rate dilution coupled to free ribosome availability:
  $$\mu(t) = \mu_{\text{max}} \cdot \frac{R_{\text{free}}}{R_{\text{total}}}$$
- `maturation_rate` ($k_{\text{mat}}$) represents the first-order protein folding and maturation delay rate.

其中：
- `copy_number` ($C$) 縮放質體所攜帶的啟動子資源需求與轉錄速率。
- `mu` ($\mu(t)$) 為動態的生長稀釋率，耦合至游離核糖體比例：
  $$\mu(t) = \mu_{\text{max}} \cdot \frac{R_{\text{free}}}{R_{\text{total}}}$$
- `maturation_rate` ($k_{\text{mat}}$) 代表一階蛋白質折疊與成熟延遲速率。

Resource occupancy is used as a warning signal for coarse burden:

資源佔用率被用作粗略負載的警告訊號：

```text
rnap_occupancy = 1.0 - rnap_free / rnap_total
ribosome_occupancy = 1.0 - ribosome_free / ribosome_total
```

This resource model is useful for detecting candidates that may demand too much transcriptional or translational capacity, and now couples growth dilution dynamically to ribosome occupancy. It does not model full cell division cycle, energy metabolism, amino-acid availability, stress responses, or detailed metabolic feedback.

### 6.1 Baseline-relative resource model (opt-in research preview)

Model version `1.10.0` adds the deterministic ODE mode `baseline_relative_v0.1`. The legacy preview remains the default. This mode represents host background demand explicitly and reports the candidate's free RNAP and ribosome fractions relative to a host-only/empty-vector baseline. Therefore, zero synthetic load has relative growth `1.0` and synthetic capacity loss `0.0`, even though the host background still occupies resources.

模型版本 `1.10.0` 新增確定性 ODE 模式 `baseline_relative_v0.1`；既有 legacy preview 仍是預設。此模式明確表示宿主背景需求，並將候選設計的游離 RNAP 與核糖體比例，正規化到 host-only／empty-vector 基準。因此，在合成負載為零時，相對生長率為 `1.0`、合成容量損失為 `0.0`，但宿主背景仍會占用資源。

Until a host calibration is supplied, the default baseline-free fractions are fixed assumptions (`0.80` for RNAP and `0.70` for ribosomes), not experimentally measured values. Callers may override them, and the resolved values and sources are returned in the resource-model contract. Outputs are relative screening indicators; they are not absolute growth-rate or proteome-allocation predictions.

在尚未提供宿主校準資料前，預設基準游離比例是固定假設（RNAP `0.80`、核糖體 `0.70`），不是實驗量測值。呼叫端可覆寫這些值，而解析後的數值與來源會記錄於 resource-model contract。輸出僅適合作為相對篩選指標，不代表絕對生長速率或蛋白質體資源分配預測。

Translation demand is weighted by mRNA abundance, RBS/translation strength, and ribosome residence time (`protein_length_aa / elongation_rate_aa_s`). Protein length is resolved from a biokinetic override, topology-level amino-acid or CDS-base-pair metadata, construct metadata, or—only as an explicitly warned fallback—a fixed `250 aa` assumption.

轉譯需求會依 mRNA 量、RBS／translation strength，以及核糖體停留時間（`protein_length_aa / elongation_rate_aa_s`）加權。蛋白質長度依序可來自 biokinetic override、topology 的胺基酸或 CDS bp metadata、construct metadata；若皆缺少，才使用明確警告的固定 `250 aa` 假設。

This baseline-relative mode currently applies to deterministic and noisy ODE simulations. The stochastic path rejects it explicitly until calibrated propensities are implemented. Both the legacy occupancy output and the new baseline-relative summary are returned side by side to support comparison and rollback.

目前 baseline-relative 模式適用於 deterministic 與 noisy ODE 模擬。stochastic 路徑會明確拒絕此模式，直到完成校準過的 propensity 實作。輸出會並列保留 legacy occupancy 與新的 baseline-relative summary，以便比較與回退。

### 6.2 Plate-reader preprocessing assumptions (M2 research preview)

Resource-calibration preprocessing version `0.1.0` expects long-format raw measurements and a separate, frozen plate map. Blank correction uses the median of all wells marked `blank` within the same experiment, plate, and timepoint. Negative blank-corrected values are clamped to zero and reported as warnings; raw and corrected channel values remain available in trace provenance.

資源校準 preprocessing `0.1.0` 預期使用長格式原始量測與獨立、固定的 plate map。Blank correction 使用同一 experiment、plate 與 timepoint 內所有 `blank` wells 的中位數。校正後的負值會截為零並產生警告；原始值、blank 值與校正值仍保留於 trace provenance。

The exponential-growth window is selected by configurable blank-corrected OD600 bounds. Growth rate is the slope of `log(OD600)` against time in hours, and wells are excluded when too few points remain, growth is non-positive, or the log-linear fit falls below the configured R-squared threshold. These rules are QC heuristics, not proof that cells were in a biologically ideal steady exponential phase.

指數生長區間依可設定的 blank-corrected OD600 範圍選取。生長率由 `log(OD600)` 對小時時間的斜率估計；若有效點數不足、生長率非正，或 log-linear fit 低於設定的 R-squared 門檻，該 well 會被排除。這些規則是 QC heuristic，不代表細胞已被證明處於理想、穩態的指數生長期。

Replicate QC groups samples by construct and inducer condition, identifies growth-rate deviations from the group median, and checks the coefficient of variation after outlier filtering. Capacity and output signals are reported relative to blank-corrected OD600. Capacity-loss metrics use the empty-vector capacity median from the same experiment and plate; they do not normalize across incompatible instruments, gains, hosts, media, or protocols.

Replicate QC 依 construct 與 inducer condition 分組，先檢查相對於組內生長率中位數的偏離，再檢查 outlier filtering 後的變異係數。Capacity 與 output signal 以 blank-corrected OD600 正規化。Capacity-loss metric 使用同一 experiment 與 plate 的 empty-vector capacity 中位數，不跨不相容的儀器、gain、宿主、培養基或 protocol 正規化。

### 6.3 Resource parameter-fitting assumptions (M3 research preview)

Fitting model `resource_competition_fit_v0.1` estimates two positive combined parameters in log space: `aggregate_demand_coefficient` and `normalized_growth_coupling`. Its fixed observation equations are `capacity_loss = k_d d / (1 + k_d d)` and `relative_growth = exp(-k_g capacity_loss)`. This first slice does not separately identify transcriptional and translational demand, nor fit RNAP/ribosome totals, Km values, elongation rates, degradation, or maturation.

Fitting model `resource_competition_fit_v0.1` 以 log space 估計兩個正值合併參數：`aggregate_demand_coefficient` 與 `normalized_growth_coupling`。固定觀測方程為 `capacity_loss = k_d d / (1 + k_d d)` 與 `relative_growth = exp(-k_g capacity_loss)`。此切片不會分別辨識 transcriptional／translational demand，也不擬合 RNAP／ribosome totals、Km、elongation rate、degradation 或 maturation。

The optimizer is bounded SciPy trust-region least squares with replicate-level uncertainty weights. A fit is publishable as an immutable research-preview profile only when the optimizer succeeds, at least three distinct positive demand levels span a two-fold range, the Jacobian has full rank and acceptable conditioning, no estimate is pinned to a bound, and a deterministic replicate-stratified bootstrap produces enough successful samples for 95% intervals.

Optimizer 使用有界的 SciPy trust-region least squares，並以 replicate-level uncertainty 加權。只有在 optimizer 成功、至少三個正 demand levels 且涵蓋兩倍 dynamic range、Jacobian full rank 且 conditioning 可接受、估計值未卡在 bound，以及 deterministic replicate-stratified bootstrap 能產生足夠成功樣本與 95% interval 時，才會建立可用的 immutable research-preview profile。

Failure of any identifiability gate yields `non_identifiable`, suppresses confidence intervals, and sets `usable_for_prediction=false`. Even an identifiable profile is labelled `fitted_to_synthetic_or_local_dataset_not_heldout_validated`; held-out predictive claims require M4. Default-versus-fitted deltas are diagnostic comparisons and are never applied automatically to the ODE model.

任何 identifiability gate 未通過時，結果會標為 `non_identifiable`、不發布 confidence interval，並設定 `usable_for_prediction=false`。即使可辨識，profile 仍標示為 `fitted_to_synthetic_or_local_dataset_not_heldout_validated`；預測性主張必須等到 M4 held-out validation。Default-versus-fitted 差異只作診斷，不會自動套用到 ODE model。

### 6.4 Held-out validation assumptions (M4 research preview)

Validation version `heldout_validation_v0.1` accepts a frozen M0 `ValidationSplit`, the exact M3 training observations, and separate held-out observations. It verifies the immutable profile's source IDs and input fingerprint before prediction, rejects observation overlap, enforces construct membership in the declared partitions, and blocks context mismatch. The validation path never refits parameters.

Validation version `heldout_validation_v0.1` 使用 frozen M0 `ValidationSplit`、與 M3 完全相同的 training observations，以及獨立 held-out observations。執行前會核對 immutable profile 的 source IDs 與 input fingerprint、拒絕 observation overlap、強制 construct 符合宣告 partition，並阻擋 context mismatch。Validation path 絕不重新擬合參數。

Versioned engineering gates are: held-out burden Spearman correlation at least `0.70`, relative-growth median absolute percentage error at most `0.20`, output fold-change direction accuracy at least `0.80`, combined capacity/growth prediction-interval coverage at least `0.80`, and improved growth error over a training-median baseline. Prediction intervals propagate the M3 marginal bootstrap bounds and add the declared observation sigma; they are an engineering coverage check, not a full posterior predictive distribution.

版本化工程 gates 包含：held-out burden Spearman correlation 至少 `0.70`、relative-growth median absolute percentage error 至多 `0.20`、output fold-change direction accuracy 至少 `0.80`、capacity／growth 合併 prediction-interval coverage 至少 `0.80`，以及 growth error 必須優於 training-median baseline。Prediction interval 會傳播 M3 marginal bootstrap bounds 並加入宣告的 observation sigma；這是工程 coverage check，不是完整 posterior predictive distribution。

Capacity and growth predictions come directly from the frozen M3 profile. Output fold-change validation requires a separate explicit prediction mapping and `output_prediction_model_id`; missing output predictions make that gate `not_evaluable` and force an overall no-go. A go report permits only `calibrated_comparative_predictor_for_stated_context`. Failed gates retain the narrower fitted-only claim, and a non-identifiable M3 profile can never be promoted by favorable held-out observations.

Capacity 與 growth prediction 直接來自 frozen M3 profile。Output fold-change validation 必須提供獨立 prediction mapping 與 `output_prediction_model_id`；缺少時該 gate 為 `not_evaluable`，整體必須 no-go。Go report 只允許 `calibrated_comparative_predictor_for_stated_context`。未通過 gates 時維持較窄的 fitted-only claim；non-identifiable M3 profile 不會因 held-out observations 看似良好而被升級。

此資源模型有助於檢測可能需求過多轉錄或翻譯能力的候選方案，且現在將生長稀釋率與核糖體佔用率進行了動態耦合。它並不模擬完整的細胞分裂週期、能量代謝、胺基酸可用性、應激反應或詳細的代謝反饋。

## 7. Parameter Assumptions/參數假設

Biokinetic parameters are attached by `DataMinerAgent`. If a vector retriever provides relevant local records, those can override defaults. Otherwise, the system uses conservative default parameters.

生物動力學參數由 `DataMinerAgent` 附加。如果向量檢索器提供了相關的本地記錄，這些記錄可以覆蓋預設值。否則，系統將使用保守的預設參數。

Current default parameters include:

目前的預設參數包括：

- `rnap_total` (total RNAP concentration / 總 RNAP 濃度, nM)
- `ribosome_total` (total ribosome concentration / 總核糖體濃度, nM)
- `km_rnap` (Michaelis constant for RNAP / RNAP 的米氏常數, nM)
- `km_ribosome` (Michaelis constant for ribosome / 核糖體的米氏常數, nM)
- `transcription_rate` (transcription rate / 轉錄速率, nM/s)
- `translation_rate` (translation rate / 翻譯速率, 1/s)
- `mrna_degradation_rate` (mRNA degradation rate / mRNA 降解速率, 1/s)
- `protein_degradation_rate` (protein degradation rate / 蛋白質降解速率, 1/s)
- `maturation_rate` (protein maturation rate / 蛋白質成熟速率, 1/s)
- `growth_rate_dilution` (maximum growth rate dilution / 最大生長稀釋速率, 1/s)
- `kd` / `kd_<node_id>` (dissociation constant / 解離常數, nM; can be loaded from Cello UCF response functions)
- `hill_coefficient` / `hill_coefficient_<node_id>` (Hill coefficient / 希爾係數, dimensionless; can be loaded from Cello UCF response functions)
- `leak_fraction` / `leak_fraction_<node_id>` (promoter leak fraction / 啟動子洩漏比例, dimensionless; computed as ymin/ymax from Cello UCF)
- `burden_soft_limit` (burden penalty threshold / 負載懲罰閾值, nM)
- `toxicity_threshold` (toxicity threshold / 毒性閾值, nM)
- `copy_number` (plasmid copy number / 質體複製數, default: 1.0)
- `protein_degradation_rate_<part_id>` / `protein_degradation_rate_<node_id>` (specific protein degradation rate / 特異性蛋白質降解速率, 1/s; dynamically scaled by active degradation tags like LVA/LAV/ASV)

Chassis-Specific Defaults:
DataMinerAgent supports host-specific overrides for two major chassis models:
- **Escherichia coli** (default):
  - `rnap_total`: 5000.0 nM
  - `ribosome_total`: 25000.0 nM
  - `translation_rate`: 0.045 1/s
  - `growth_rate_dilution`: 0.0004 1/s
- **Saccharomyces cerevisiae** (yeast):
  - `rnap_total`: 3000.0 nM
  - `ribosome_total`: 120000.0 nM
  - `translation_rate`: 0.012 1/s
  - `growth_rate_dilution`: 0.0001 1/s

宿主特異性預設值：
DataMinerAgent 支援針對兩種主要宿主模型的特異性參數覆蓋：
- **大腸桿菌 (Escherichia coli)**（預設）：
  - `rnap_total`: 5000.0 nM
  - `ribosome_total`: 25000.0 nM
  - `translation_rate`: 0.045 1/s
  - `growth_rate_dilution`: 0.0004 1/s
- **釀酒酵母 (Saccharomyces cerevisiae)**（酵母）：
  - `rnap_total`: 3000.0 nM
  - `ribosome_total`: 120000.0 nM
  - `translation_rate`: 0.012 1/s
  - `growth_rate_dilution`: 0.0001 1/s

The current unit system is:

目前的單位系統為：

```text
nM and seconds
nM 和秒
```

Default parameters are useful for keeping the workflow executable, but they limit biological interpretation. Parameter provenance matters: a simulation based on host-specific, literature-supported, or experimentally calibrated parameters should be considered stronger than one based only on defaults.

預設參數對於保持工作流的可執行性非常有用，但它們限制了生物學上的解讀。參數來源（Provenance）至關重要：基於宿主特異性、文獻支持或實驗校準參數的模擬，應被視為比僅基於預設參數的模擬更具說服力。

## 8. Numerical Integration Assumptions/數值積分假設

The simulator first tries SciPy stiff ODE solvers:

模擬器首先嘗試 SciPy 的剛性（Stiff）ODE 求解器：

1. `BDF`
2. `Radau`

If SciPy is unavailable or the solver path fails in the current environment, the implementation can fall back to an internal RK4-style integration path.

若 SciPy 不可用或求解器路徑在當前環境下失敗，該實作可以回退到內部的 RK4 風格積分路徑。

A successful integration means the model equations were solved numerically under the provided assumptions. It does not mean the candidate is experimentally buildable or biologically valid.

成功的積分代表模型方程在提供的假設下得到了數值解。它並不意味著候選方案在實驗上是可構建的，或在生物學上是有效的。

## 9. Noise and Monte Carlo Assumptions/雜訊與蒙特卡羅假設

Monte Carlo perturbation is used as a sensitivity screen. It perturbs selected kinetic parameters by sampling around their current values. For standard biokinetic parameters, normal distribution perturbation is used:

蒙特卡羅微擾被用作敏感性篩選。它通過在其當前值周圍進行抽樣來對選定的動力學參數進行微擾。對於標準生物動力學參數，使用常態分佈微擾：

```text
sampled_value = max(0.0, normal(original_value, abs(original_value) * noise_level))
```

For plasmid copy number (`copy_number`), log-normal perturbation is used to preserve the arithmetic mean and ensure strictly positive values:

對於質體複製數 (`copy_number`)，使用對數常態（Log-normal）微擾，以保留其算術平均值並確保數值恆大於零：

```text
sampled_value = original_value * exp(normal(0.0, s) - 0.5 * s * s)
where s = sqrt(log(1.0 + noise_level^2))
```

Perturbable parameters include:

可微擾的參數包括：

- `transcription_rate`
- `translation_rate`
- `kd`
- `hill_coefficient`
- `leak_fraction`
- `mrna_degradation_rate`
- `protein_degradation_rate`
- `y_min`
- `ymax`
- `y_max`
- `copy_number`

The noisy-response scorer compares terminal output against an early output window:

有雜訊響應評分器將終端輸出與早期輸出窗口進行對比：

```text
on_value = terminal output
off_value = max(output in early window)
```

It treats a candidate as collapsed if:

若滿足以下條件，它會將候選方案視為塌陷（Collapsed）：

```text
max(off_values) >= min(on_values)
```

This is a simple robustness heuristic. It is not a measured noise model and does not represent experimentally observed cell-to-cell variability.

這是一個簡單的魯棒性啟發式指標。它不是一個測量得出的雜訊模型，也不代表實驗觀察到的細胞間變異性。

## 10. Output Interpretation/輸出結果的解讀

Important output fields should be interpreted conservatively:

重要的輸出欄位應進行保守解讀：

| Output / 輸出欄位 | Interpretation / 解讀 |
| --- | --- |
| `ode_status = "simulated"` | Numerical simulation completed under the model assumptions. <br> 數值模擬在模型假設下完成。 |
| `kinetic_score` | Relative ODE-derived screening score. <br> 相對的 ODE 導出篩選分數。 |
| `robustness_score` | Sensitivity score under implemented perturbations. <br> 在實現的微擾下的敏感性分數。 |
| `dynamic_margin` | Approximate output separation proxy. <br> 近似的輸出分離代理指標。 |
| `signal_to_noise_ratio` | Simulation-derived output variation proxy. <br> 模擬導出的輸出變異代理指標。 |
| `resource_occupancy` | Coarse RNAP/ribosome burden signal. <br> 粗略的 RNAP/核糖體負載訊號。 |
| `metrics_max_burden` | Aggregate mRNA/protein burden proxy. <br> 聚合 mRNA/蛋白質負載代理指標。 |
| `parameter_provenance` | Evidence quality for the parameter values used. <br> 所用參數值的證據品質。 |
| `ode_explanation.key_readouts` | Derived trajectory readouts such as peak output, time to peak, final output, fold-change proxy, and steady-state status. <br> 衍生的軌跡讀數，例如最大輸出、達峰時間、最終輸出、fold-change 代理指標與穩態狀態。 |
| `ode_explanation.burden_readouts` | Coarse mRNA/protein and RNAP/ribosome burden readouts. <br> 粗略的 mRNA/蛋白質與 RNAP/核糖體負載讀數。 |
| `ode_explanation.coverage_warnings` | Warnings about missing explicit input scenarios, OFF-state traces, ON/OFF ratios, or Monte Carlo perturbation. <br> 關於缺少明確輸入情境、OFF-state 軌跡、ON/OFF 比率或蒙特卡羅微擾的警示。 |
| `simulation_status = "truncated"` | One or more stochastic runs reached the configured step limit before the requested end time; this is incomplete evidence. <br> 至少一個隨機 run 在要求的終止時間前達到步數上限，因此證據不完整。 |
| `retroactivity_max` | Simplified regulator-sequestration load index used for relative screening. <br> 用於相對篩選的簡化調控因子 sequestration 負載指標。 |
| `rbs_blocking_detected` | Heuristic sequence-accessibility warning, not a thermodynamic folding prediction. <br> 啟發式序列可及性警告，不是熱力學折疊預測。 |

These fields are most useful for comparing candidates generated in the same workflow. They should not be treated as standalone experimental predictions.

這些欄位最有用於比較在相同工作流中生成的候選方案。它們不應被視為獨立的實驗預測。

## 11. Mechanisms Not Currently Modeled/目前未建模的機制

The current model does not include:

當前的模型不包括：

- complete plasmid sequence or backbone architecture;
  完整的質體序列或骨架架構；
- origin of replication, selectable marker, dynamic copy-number variance (e.g. noise, segregational instability), or assembly scars (plasmid copy number scaling is modeled statically);
  複製起點、篩選標記、動態複製數變異如雜訊或分配不均、以及組裝疤痕（質體複製數縮放目前為靜態建模）；
- promoter-, RBS-, terminator-, or coding-sequence-level design constraints;
  啟動子、RBS、終止子或編碼序列級別的設計約束；
- host cell division cycle, cell-cycle effects, or global metabolic regulation (dilution by host growth is modeled via ribosome-growth coupling);
  宿主細胞分裂週期、細胞週期效應或全局代謝調節（宿主生長稀釋目前已透過核糖體-生長耦合進行建模）；
- stress-response feedback (ribosome-growth dilution coupling is modeled, but complex cellular stress responses are not);
  應激反應反饋（核糖體-生長稀釋耦合已建模，但複雜的細胞應激反應尚未建模）；
- DNA supercoiling or local sequence context;
  DNA 超螺旋或本地序列上下文；
- full thermodynamic RNA folding, RNA stability motifs, or transcript processing (a local heuristic can flag strong RBS-region hairpins, but it is not ViennaRNA/NUPACK-equivalent validation);
  完整熱力學 RNA 折疊、RNA 穩定性基序或轉錄本處理（本地啟發式方法可警告強 RBS 區域 hairpin，但不等同 ViennaRNA／NUPACK 驗證）；
- detailed codon-pair bias, translation initiation context, or ribosome traffic (synonymous E. coli codon replacement is now supported at the sequence level, but not dynamically simulated in ODEs);
  詳細的密碼子對偏好、翻譯起始上下文或核糖體交通（目前已在序列層級支援大腸桿菌同義密碼子替換，但尚未在 ODE 中進行動態模擬）；
- detailed protein folding pathways, chaperones, degradation tags, or active/inactive maturation states (modeled via a simplified first-order maturation rate delay);
  詳細的蛋白質折疊路徑、分子伴侶、降解標籤或活性/非活性成熟狀態（目前透過簡化的一階成熟速率延遲進行建模）；
- inducer transport, ligand binding, or environmental dynamics;
  誘導物轉運、配體結合或環境動力學；
- experimentally calibrated toxicity and noise distributions.
  經實驗校準的毒性與雜訊分布。

These omissions are intentional for the current prototype. The model is kept small enough to run inside an iterative search loop.

對於目前的原型，這些省略是刻意為之的。模型保持足夠小的規模，以便在迭代搜尋迴圈中運行。

## 12. Evidence Needed for Stronger Biological Claims/做出更強生物學宣稱所需的證據

To move from computational candidate ranking toward stronger biological claims, future versions should add:

為了從計算候選方案排序邁向更強的生物學宣稱，未來的版本應增加：

- real Cello execution with appropriate UCF files and mapped part assignments;
  使用適當的 UCF 文件和映射的元件分配進行真實的 Cello 執行；
- sequence-level checks for promoters, RBSs, coding regions, terminators, and cloning constraints;
  針對啟動子、RBS、編碼區、終止子與克隆約束的序列級檢查；
- host-specific parameter calibration from literature or experiments;
  來自文獻或實驗的宿主特異性參數校準；
- experimentally calibrated plasmid copy-number and growth-dilution dynamics;
  經實驗校準的質體複製數與生長稀釋動力學；
- experimental burden-growth feedback and toxicity calibration;
  實驗性負載-生長反饋與毒性校準；
- experimentally measured ON/OFF ratios, response times, noise, burden, and growth effects;
  經實驗測量的 ON/OFF 比率、響應時間、雜訊、負載與生長效應；
- comparison against known measured genetic circuits;
  與已知經測量的基因電路進行對比；
- validation of score weights against empirical outcomes.
  根據經驗結果驗證評分權重。

Until those steps are added, the appropriate claim is:

在增加這些步驟之前，合適的宣稱為：

> The model provides simplified screening evidence for computational candidate designs.
> 該模型為計算候選設計提供簡化的篩選證據。

It should not be described as:

它不應被描述為：

> A quantitative predictor of complete, buildable, experimentally validated genetic circuits.
> 完整、可構建且經過實驗驗證 of 基因電路的定量預測器。
## 13. Relationship Between DesignIR and the ODE Model (2026-06-06)
## 13. DesignIR 與 ODE 模型的關係（2026-06-06）

The new part, revision, comparison, and export layers do not expand the biological scope of the ODE model.

新增的元件、版本、比較與匯出層不會擴張 ODE 模型的生物學範圍。

- `DesignIR` provides a richer representation of candidate parts and constructs.
- `DesignIR` 提供較完整的候選元件與 construct 表示。
- Part replacement changes stored assignment and sequence metadata, but the current ODE simulator does not automatically derive calibrated kinetic parameters from those sequences.
- 元件替換會改變保存的 assignment 與 sequence metadata，但目前 ODE simulator 不會自動從序列推導經校準的動力學參數。
- `DesignDiff` can display score differences, but those scores remain based on the existing benchmark and simulation assumptions.
- `DesignDiff` 可以顯示分數差異，但這些分數仍基於既有 benchmark 與模擬假設。
- BOM, GenBank, and SBOL3 are representations of current data; they do not add biological mechanisms or improve parameter calibration.
- BOM、GenBank 與 SBOL3 是目前資料的表示，不會增加生物機制或改善參數校準。

Therefore, a sequence-backed export should not be interpreted as a sequence-aware ODE prediction. Stronger coupling would require part-specific response functions, promoter/RBS models, degradation parameters, copy-number context, and calibrated host data.

因此，具有序列的匯出不應解讀為 sequence-aware ODE 預測。更強的耦合需要元件特定 response function、promoter/RBS 模型、降解參數、copy-number 情境與經校準的宿主資料。

## 14. Sequence and Host Optimization Assumptions (2026-06-16)
## 14. 序列與宿主優化假設（2026-06-16）

The new synonymous codon-optimization, host profiling, and experimental calibration layers introduce additional computational models with specific assumptions:

新增的同義密碼子優化、宿主設定檔與實驗校準層引入了具有特定假設的額外計算模型：

- **Synonymous CDS Replacement**: Verifies preservation of the translated amino-acid sequence. It does not establish unchanged translation rate, folding, expression, stability, or function. The ODE maturation-delay parameters remain unchanged because the current model does not derive them from synonymous codon choices.
  **同義 CDS 替換**：只驗證翻譯後胺基酸序列保持不變；不代表翻譯速率、折疊、表達量、穩定性或功能不變。由於目前模型不會由同義密碼子選擇推導成熟延遲參數，ODE 參數維持原值。
- **Motif Avoidance**: Attempts synonymous removal of configured restriction/Type IIS sites. Passing this check does not demonstrate unchanged translation efficiency or biological function.
  **基序避免**：嘗試以同義替換移除設定的限制酶切／Type IIS 位點；通過檢查不代表翻譯效率或生物學功能保持不變。
- **Candidate Ranking Strategies**: Matches E. coli candidates under high-expression, low-burden, and balanced strategies to fixed copy-number classes, promoter strengths, and RBS strengths. Biases (expression/burden/stability) are static scores and do not dynamically simulate cellular trajectories.
  **候選方案排序策略**：將在高表達、低負載與平衡策略下的大腸桿菌候選方案與固定的複製數類別、啟動子強度及 RBS 強度相匹配。偏置分數（表達量/負載/穩定性）是靜態分數，不會動態模擬細胞軌跡。
- **Calibration Model Independence**: Experimental calibration summaries evaluate dataset coverage and average performance; they do not calibrate parameters dynamically in the ODE simulation.
  **校準模型獨立性**：實驗校準摘要評估數據集覆蓋率和平均表現；它們目前不會動態調整 ODE 模擬中的參數。

## 15. Advanced Biophysical Modeling Assumptions & Structural Boundaries (2026-07-07)
## 15. 高階生物物理建模假設與結構界限（2026-07-07）

To maintain rigorous academic boundary limits, the following biophysical assumptions and structural constraints are explicitly defined:

為了維持嚴格的學術邊界與限制，此處對以下生物物理假設與結構約束進行明確界定：

- **Context-Dependency of Cello-derived Parameters**: Kinetic parameters (such as $y_{\text{min}}, y_{\text{max}}, K_d, n$) parsed from Cello UCFs are characterized under specific experimental biological contexts (media, host strains, promoter configurations, plasmid backbones). Mapping them directly into a general ODE model assumes perfect context-independence, neglecting transcript-level loading or resource sequestering in different host contexts.
  **Cello 參數的「上下文相依性」**：從 Cello UCF 解析出的動力學參數（如 $y_{\text{min}}, y_{\text{max}}, K_d, n$）是在特定的實驗生物上下文（如培養基、宿主菌株、啟動子配置、質體骨架）中測量得到的。直接將其應用至一般的 ODE 模型，是建立在「元件完美獨立」的簡化假設之上，忽略了不同宿主背景下的轉錄級加載或資源分配干擾。
- **Heuristic RBS Accessibility vs. Thermodynamic Folding**: Sequence-level warnings regarding RBS folding or secondary structure accessibility are computed as sequence-window heuristic proxies. These scores do not evaluate full partition functions or thermodynamic translation initiation rates, and are not equivalent to thermodynamic solvers such as NUPACK or the Salis RBS Calculator.
  **啟發式 RBS 可及性與熱力學折疊**：序列層級關於 RBS 折疊或二級結構可及性的警示，是作為序列窗口的啟發式指標計算出來的。這些分數並非對完整配分函數或熱力學轉譯起始速率的評估，亦不等同於 NUPACK 或 Salis RBS Calculator 等熱力學求解器。
- **Deterministic Continuous Limit vs. Chemical Master Equation**: The continuous ODE model assumes the thermodynamic limit of high molecule numbers. While Gillespie stochastic auditing is provided as a bounded numerical checker, it only identifies stochastic extinction or instability under low-molecule constraints (e.g., low-copy plasmids or weak expression); it is not a full solution to the Chemical Master Equation (CME) and does not model cell-to-cell heterogeneity or partition noise comprehensively.
  **確定性連續極限與化學主方程式**：連續的 ODE 模型假設了高分子數的熱力學極限。雖然 Gillespie 隨機稽核作為一個有限的數值檢查器被提供，但它僅用於辨識低分子量限制下（如低拷貝質體或弱表達）的隨機絕滅或不穩定性；它並非對化學主方程（CME）的完整求解，也不代表對細胞間異質性或分配噪聲的全面建模。
- **Metabolic Burden Proxy Limits**: The synthetic metabolic burden metric ($burden\_nM$) represents a coarse-grained concentration proxy of synthetic components rather than a dynamic coupling to internal host pathways, dynamic ribosomal/RNAP synthesis feedback, growth rate decay dynamics, or cellular stress responses.
  **代謝負載代理指標限制**：合成代謝負載指標（$burden\_nM$）僅代表合成組件的粗粒度濃度代理值，並非與宿主內源代謝途徑、動態核糖體/RNAP 合成反饋、生長速率衰減動力學或細胞應激反應的動態耦合。

## References

1. Nielsen, A. A. K. et al. “Genetic circuit design automation.” *Science*
   352, aac7341 (2016). https://doi.org/10.1126/science.aac7341
2. Gillespie, D. T. “Exact stochastic simulation of coupled chemical
   reactions.” *The Journal of Physical Chemistry* 81, 2340-2361 (1977).
   https://doi.org/10.1021/j100540a008
3. Frei, T. et al. “Predicting composition of genetic circuits with resource
   competition: demand and sensitivity.” *ACS Synthetic Biology* 10,
   3330-3342 (2021). https://doi.org/10.1021/acssynbio.1c00281
4. Baig, H. et al. “Synthetic biology open language (SBOL) version 3.0.0.”
   *Journal of Integrative Bioinformatics* 17 (2020).
   https://doi.org/10.1515/jib-2020-0017

## Resource calibration workflow assumptions (M5 research preview)

Workflow version `resource_calibration_workflow_v0.1` orchestrates context review, optional raw plate-reader preprocessing, observation partitioning, M3 fitting, and M4 frozen held-out validation. `raw_plate_reader` inputs retain plate-map fingerprints and raw trace counts. `derived_observations` inputs are accepted only as a governed research path and are explicitly labelled `not_run_prederived_input`; this path does not imply that raw plate-reader QC occurred.

工作流會把 `raw_plate_reader` 與 `derived_observations` 分開呈現。後者僅供受控研究資料使用，必須保留 observation ID 與 source metric ID，且不得宣稱已完成原始 plate-reader QC。

Each persisted workflow reports stage status, the dominant limiting layer, observed/calibrated/defaulted/inferred counts, provenance, warnings, default-versus-fitted parameter deltas, and held-out candidate comparisons when available. Invalid experimental designs are saved as explainable `blocked` or `no_go` reports rather than promoted profiles.

Even a `go` decision is limited to `calibrated_comparative_predictor_for_stated_context`. The workflow sets `automatic_application.allowed=false`; no API or UI operation in M5 updates the production ODE model or part library. A separate manual review and future promotion contract would be required.

## Global sensitivity and model-comparison assumptions (M6 research preview)

Morris screening uses deterministic normalized one-at-a-time trajectories across the two combined M3 parameters. Its `mu`, `mu_star`, and `sigma` values are screening diagnostics, not a variance decomposition. Rankings are conditional on the recorded parameter ranges; changing those ranges can change the ranking.

The Sobol path is a low-cost Saltelli-style Monte Carlo pilot. It reports finite-sample raw indices and clipped values for ranking, but it does not yet provide convergence intervals. Negative raw first-order indices can occur from Monte Carlo error and must not be interpreted as negative biological influence.

Model comparison uses one immutable training/validation split and compares constant-training-median, clipped linear-demand, and `resource_competition_fit_v0.1` predictions. A win on one synthetic or local holdout does not establish universal model superiority. The current SBML/BioCRNpyler gate remains no-go until the same real pilot data can test predictive improvement, identifiability, runtime cost, and external-solver equivalence.

M6 sets `automatic_model_promotion.allowed=false`. Its output is a model-selection diagnostic and research-planning aid, not permission to replace the production simulator.
