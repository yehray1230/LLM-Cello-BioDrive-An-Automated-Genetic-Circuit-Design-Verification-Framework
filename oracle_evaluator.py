import numpy as np
import pandas as pd

class CircuitEvaluator:
    def __init__(self, on_threshold=1000.0, off_threshold=100.0):
        self.on_threshold = on_threshold
        self.off_threshold = off_threshold

    def extract_steady_state(self, time_series: np.ndarray) -> float:
        """取的最後 10% 時間的平均值作為穩定收斂狀態"""
        if len(time_series) == 0:
            return 0.0
        tail_idx = int(len(time_series) * 0.9)
        return float(np.mean(time_series[tail_idx:]))

    def detect_oscillation(self, time_series: np.ndarray) -> bool:
        """利用微積分過零點的次數初步判定是否震盪"""
        if len(time_series) < 100:
            return False
        tail_idx = int(len(time_series) * 0.5) # 觀察後半段
        tail_data = time_series[tail_idx:]
        
        mean_val = np.mean(tail_data)
        zero_crossings = np.where(np.diff(np.sign(tail_data - mean_val)))[0]
        
        # 若有跨越均線超過至少 3 次且波峰具有一定振幅
        amplitude = np.max(tail_data) - np.min(tail_data)
        if len(zero_crossings) >= 3 and amplitude > self.off_threshold:
            return True
        return False

    def evaluate_results(self, ode_results: dict, target_spec: dict, target_species: str) -> dict:
        """
        ode_results = {"state_name": DataFrame, ...}
        target_spec = {"state_name": expected_bool, ...} (例如 {"00_state": 0, "01_state": 1})
        """
        if not ode_results or not target_spec:
            return {
                "pass": False,
                "score": 0.0,
                "feedback_string": "【ODE 驗證失敗】無完整的測試結果或目標真值表。",
                "fold_change": 0.0
            }
            
        all_steady_states = {}
        for state_name, df in ode_results.items():
            if target_species not in df.columns:
                return {
                    "pass": False,
                    "score": 0.0,
                    "feedback_string": f"【ODE 驗證失敗】找無目標輸出節點 '{target_species}' 的資料。",
                    "fold_change": 0.0
                }
            
            time_series = df[target_species].values
            steady_state = self.extract_steady_state(time_series)
            all_steady_states[state_name] = steady_state
            
        lowest_on = float('inf')
        highest_off = -float('inf')
        
        logic_errors = []
        leakage_errors = []
        
        for state_name, expected in target_spec.items():
            val = all_steady_states.get(state_name, 0.0)
            
            if expected == 1:
                lowest_on = min(lowest_on, val)
                if val < self.on_threshold:
                    logic_errors.append((state_name, val, "ON"))
            else:
                highest_off = max(highest_off, val)
                if val > self.off_threshold:
                    leakage_errors.append((state_name, val, "OFF"))
                    
        fold_change = 0.0
        if highest_off > 0:
            fold_change = lowest_on / highest_off
            
        if not logic_errors and not leakage_errors and fold_change >= (self.on_threshold / self.off_threshold):
            return {
                "pass": True,
                "score": 100.0,
                "feedback_string": "【ODE 驗證成功】符合預期邏輯行為且 Fold Change 達標。",
                "fold_change": fold_change,
                "steady_states": all_steady_states
            }
            
        # 產生錯誤文本
        return self.generate_semantic_feedback(logic_errors, leakage_errors, fold_change, all_steady_states)

    def generate_semantic_feedback(self, logic_errors, leakage_errors, fold_change, all_states) -> dict:
        feedbacks = []
        for state_name, val, expected in leakage_errors:
            feedbacks.append(f"在輸入條件 {state_name} 時，預期輸出為 OFF，但輸出元件的洩漏表現量達 {val:.2f} a.u. (高於 OFF 閾值 {self.off_threshold})。")
        for state_name, val, expected in logic_errors:
            feedbacks.append(f"在輸入條件 {state_name} 時，預期輸出為 ON，但模擬穩態值僅為 {val:.2f} a.u. (低於 ON 閾值 {self.on_threshold})。電路拓樸可能存在邏輯短路或反轉。")
            
        if len(feedbacks) > 0:
            prefix = "【ODE 驗證失敗】"
            feedback_str = prefix + " ".join(feedbacks) + f" 目前的 Fold Change 僅為 {fold_change:.2f} 倍。"
            if leakage_errors:
                feedback_str += "請 Builder 考慮引入更強的阻遏蛋白、增加降解標籤 (Degradation Tag)、或是更換 weaker promoter 降低 Leakage。"
            if logic_errors:
                feedback_str += "請重新檢查邏輯閘的級聯關係或增加訊號放大。"
        else:
            feedback_str = f"【ODE 驗證失敗】系統未能達到標準 Fold Change (目前為 {fold_change:.2f})。"
            
        score = max(0.0, 100.0 - 20 * len(logic_errors) - 10 * len(leakage_errors))
        return {
            "pass": False,
            "score": score,
            "feedback_string": feedback_str,
            "fold_change": fold_change,
            "steady_states": all_states
        }

    def evaluate_monte_carlo_results(self, mc_ode_results: list[dict], target_spec: dict, target_species: str, user_intent: str, noise_level: float = 0.05, mc_iterations: int = 10, pass_threshold: float = 90.0) -> dict:
        """
        計算蒙地卡羅 50 次或自訂次數模擬的綜合穩健度，並引入意圖驅動毒性評估。
        mc_ode_results: List of ode_results dictionaries
        """
        death_keywords = ["lysis", "裂解", "細胞死亡", "suicide", "cell death", "死亡", "凋亡", "death"]
        intent_lower = user_intent.lower()
        is_expected_death = any(kw in intent_lower for kw in death_keywords)
        
        TOXICITY_THRESHOLD = 50000.0 # 代謝負荷閾值 (例如所有蛋白質總濃度 50000 nM)
        
        pass_count = 0
        mc_feedbacks = []
        toxicity_failures = 0
        total_samples = len(mc_ode_results)
        
        if total_samples == 0:
            return {"pass": False, "score": 0.0, "feedback_string": "未提供任何模擬數據。"}
            
        for i, ode_results in enumerate(mc_ode_results):
            # 毒性判定: 計算所有表現的總蛋白質濃度 (扣除 mRNA 與 Input)
            max_burden = 0.0
            for state_name, df in ode_results.items():
                protein_cols = [c for c in df.columns if not c.endswith("_mRNA") and not c.startswith("Input_") and c != "Time"]
                if not protein_cols:
                    continue
                burden = df[protein_cols].sum(axis=1).max()
                if burden > max_burden:
                    max_burden = burden
                    
            if max_burden > TOXICITY_THRESHOLD and not is_expected_death:
                toxicity_failures += 1
                mc_feedbacks.append(f"樣本 {i}: UNINTENDED_BURDEN (Max burden {max_burden:.2f} > {TOXICITY_THRESHOLD})")
                continue
                
            # 一般邏輯與 Fold Change 判定
            res = self.evaluate_results(ode_results, target_spec, target_species)
            if res["pass"]:
                pass_count += 1
            else:
                mc_feedbacks.append(f"樣本 {i}: {res['feedback_string']}")
                
        pass_rate = pass_count / total_samples
        robustness = pass_rate * 100.0
        
        # 邊界條件處理
        if mc_iterations == 1 or noise_level == 0.0:
            if toxicity_failures > 0:
                overall_pass = False
                feedback_str = "【ODE 驗證失敗】在理想無噪音環境下，您的設計未能通過毒性檢測。"
            elif robustness >= 100.0:
                overall_pass = True
                death_tag = " (EXPECTED_DEATH)" if is_expected_death else ""
                feedback_str = f"【ODE 快速驗證】在理想無噪音環境下，您的設計已通過邏輯與毒性檢測。{death_tag}"
            else:
                overall_pass = False
                feedback_str = "【ODE 驗證失敗】在理想無噪音環境下，您的設計未能通過邏輯檢測。"
        else:
            if toxicity_failures > (total_samples * 0.5):
                feedback_str = f"【ODE 驗證失敗】(UNINTENDED_BURDEN) 在 {total_samples} 組變異參數中，有 {toxicity_failures} 組因蛋白質總代謝負荷（>{TOXICITY_THRESHOLD}）引發細胞毒性崩潰。請降低 promoter 強度或加入負回饋。"
                overall_pass = False
            elif robustness >= pass_threshold:
                overall_pass = True
                death_tag = " (EXPECTED_DEATH)" if is_expected_death else ""
                feedback_str = f"【ODE 壓力測試】在 {noise_level*100:.0f}% 的系統噪音下，經過 {mc_iterations} 次模擬，電路的穩健度為 {robustness:.0f}%（大於或等於及格門檻 {pass_threshold}%），驗證通過。{death_tag}"
            else:
                overall_pass = False
                feedback_str = f"【ODE 驗證警告】您的設計在壓力測試下未達標。在 {noise_level*100:.0f}% 噪音與 {mc_iterations} 次模擬中，穩健度僅為 {robustness:.0f}% (未能達到及格門檻 {pass_threshold}%)。建議重點重構電路拓樸或更換啟動子。"
            
        return {
            "pass": overall_pass,
            "pass_rate": pass_rate,
            "score": pass_rate * 100.0,
            "toxicity_failures": toxicity_failures,
            "feedback_string": feedback_str,
            "mc_feedbacks": mc_feedbacks
        }
