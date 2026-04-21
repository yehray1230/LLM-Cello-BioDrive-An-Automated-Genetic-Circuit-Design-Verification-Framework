import json
import requests
import re
from bs4 import BeautifulSoup
import litellm
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp
import sympy as sp
import asyncio
import urllib.parse

HOST_BASELINES = {
    "Escherichia coli (大腸桿菌)": {
        "transcription_rate": 8.33,
        "translation_rate": 0.033,
        "mrna_deg_rate": 0.00167,
        "protein_deg_rate": 0.00083,
        "kd": 1000.0,
        "n": 2.0
    },
    "Bacillus subtilis (枯草桿菌)": {
        "transcription_rate": 6.5,
        "translation_rate": 0.02,
        "mrna_deg_rate": 0.002,
        "protein_deg_rate": 0.0005,
        "kd": 1200.0,
        "n": 2.0
    },
    "Saccharomyces cerevisiae (釀酒酵母)": {
        "transcription_rate": 0.5,
        "translation_rate": 0.005,
        "mrna_deg_rate": 0.0005,
        "protein_deg_rate": 0.0001,
        "kd": 500.0,
        "n": 2.0
    }
}

TIME_CONVERSION = {
    "s": 1.0,
    "sec": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0
}

CONC_CONVERSION = {
    "nm": 1.0,
    "um": 1000.0,
    "mm": 1000000.0,
    "m":  1000000000.0,
    "pm": 0.001
}

def normalize_parameter(raw_value: float, raw_unit: str, param_type: str) -> float:
    """將原始帶單位的數值轉換為系統標準基準 (nM 與 seconds)。"""
    if param_type == "n":
        return float(raw_value)
    
    if not isinstance(raw_unit, str) or not raw_unit.strip():
        raise ValueError(f"Unknown unit for {param_type}: empty unit")
        
    unit_str = raw_unit.lower().strip()
    
    # Check simple time rate (1/Time) for translation_rate, protein_deg_rate, mrna_deg_rate
    if param_type in ["translation_rate", "protein_deg_rate", "mrna_deg_rate"]:
        # Expected format like "1/min", "min^-1", "s^-1", "h-1"
        time_unit = unit_str.replace("1/", "").replace("^-1", "").replace("-1", "").strip()
        if time_unit not in TIME_CONVERSION:
            raise ValueError(f"Unknown time unit: {raw_unit}")
        return float(raw_value) / TIME_CONVERSION[time_unit]
        
    # Check simple concentration for kd
    if param_type == "kd":
        if unit_str not in CONC_CONVERSION:
            raise ValueError(f"Unknown concentration unit: {raw_unit}")
        return float(raw_value) * CONC_CONVERSION[unit_str]
        
    # Check compound unit Conc/Time for transcription_rate
    if param_type == "transcription_rate":
        # Expected like uM/min
        if "/" not in unit_str:
            raise ValueError(f"Unknown compound unit: {raw_unit}")
        parts = unit_str.split("/")
        conc_part = parts[0].strip()
        time_part = parts[1].strip()
        if conc_part not in CONC_CONVERSION or time_part not in TIME_CONVERSION:
            raise ValueError(f"Unknown compound unit parts in: {raw_unit}")
        return float(raw_value) * CONC_CONVERSION[conc_part] / TIME_CONVERSION[time_part]
        
    raise ValueError(f"Unknown param_type: {param_type}")

def filter_relevant_paragraphs(raw_text: str, part_name: str) -> str:
    """
    將爬取到的 raw_text 切割成段落，並過濾只保留：
    1. 包含數字
    2. 包含生物動力學關鍵字 (例如 Kd, constant, rate, half-life, nM, uM, min, sec 等)
    可用來大幅降低丟給 LLM 解析的 Token 量。
    """
    keywords = ["kd", "constant", "rate", "half-life", "halflife", "nm", "um", "mm", "min", "sec", "hour"]
    
    # 用換行符號切割段落
    paragraphs = raw_text.split('\n')
    
    relevant_snippets = []
    for p in paragraphs:
        p_str = p.strip()
        if not p_str: 
            continue
            
        p_lower = p_str.lower()
        has_digit = any(char.isdigit() for char in p_str)
        has_keyword = any(kw in p_lower for kw in keywords)
        
        if has_digit and has_keyword:
            relevant_snippets.append(p_str)
            
    return "\n".join(relevant_snippets)


def mine_biochemical_data(parts: list[str], api_key: str | None = None, model_name: str = "gpt-4o-mini", api_base: str | None = None, host_organism: str = "Escherichia coli (大腸桿菌)") -> dict:
    """
    1. 針對指定的元件關鍵字與寄主生物，輕量爬蟲搜尋 BioNumbers
    2. 使用文本壓縮器過濾出精華段落
    3. 使用 LLM (Data Miner Agent) 萃取需要的生化參數，並強制換算單位
    4. 若無則自動填寫合理預設值並標註，回傳最終的生化參數 dict。
    """
    # 1. 輕量爬蟲 (Lightweight Scraper) & 文本過濾 (Text Snippet Extractor)
    scraped_texts = ""
    for part in parts:
        query = urllib.parse.quote(f"{part} in {host_organism}")
        url = f"https://bionumbers.hms.harvard.edu/search.aspx?task=searchbytrm&trm={query}"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                # 取得大段落，保留 \n 作為分隔符號以便文本過濾器切分
                raw_text = soup.get_text(separator='\n', strip=True)
                snippets = filter_relevant_paragraphs(raw_text, part)
                
                # 仍給予字數上限保護，避免過大
                scraped_texts += f"\n--- Search results for {part} in {host_organism} ---\n{snippets[:4000]}"
        except Exception as e:
            scraped_texts += f"\nFailed to scrape {part}: {e}"

    if not scraped_texts.strip():
        scraped_texts = "No specific database context could be retrieved."

    system_prompt = f"""You are a Biochemical Data Miner Agent.
Your task is to extract ODE simulation parameters for the provided genetic parts based on the web scraping context.
If the exact parameter is NOT found, you MUST provide a reasonable empirical default value for the host organism {host_organism} and mark `is_empirical` as true.

CRITICAL INSTRUCTION:
- You must extract the EXACT textual unit found in the context (e.g. `uM/min`, `1/hr`, `nM`, etc.) into `raw_unit` and its numeric amount into `raw_value`.
- DO NOT perform any mathematical transformations or unit normalization yourself.
- If it is a dimensionless parameter (like Hill coefficient), set `raw_unit` to `"dimensionless"`.
- If the unit cannot be deciphered, guess a reasonable standard unit and mark it empirical.

Output ONLY strictly valid JSON. Do not include markdown blocks or any other explanation outside the final JSON block.
"""

    user_prompt = f"""
【Context from BioNumbers】:
{scraped_texts}

【Target Parts】:
{', '.join(parts)}

Please extract or estimate the following parameters. Output ONLY a JSON dictionary with this exact schema (no markdown, no backticks):
{{
  "transcription_rate": {{
    "raw_value": 0.5, "raw_unit": "uM/min", "is_empirical": true
  }},
  "translation_rate": {{
    "raw_value": 2.0, "raw_unit": "1/min", "is_empirical": true
  }},
  "mrna_deg_rate": {{
    "raw_value": 0.1, "raw_unit": "1/min", "is_empirical": true
  }},
  "protein_deg_rate": {{
    "raw_value": 0.05, "raw_unit": "1/min", "is_empirical": true
  }},
  "kd": {{
    "raw_value": 1.0, "raw_unit": "uM", "is_empirical": true
  }},
  "n": {{
    "raw_value": 2.0, "raw_unit": "dimensionless", "is_empirical": true
  }}
}}
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # 3. 呼叫 LLM 萃取與填補預設值
    params = {}
    try:
        response = litellm.completion(
            model=model_name,
            messages=messages,
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            api_base=api_base.strip() if api_base and api_base.strip() else None,
            temperature=0.1
        )
        raw_response = response.choices[0].message["content"].strip()
        
        # 清理可能輸出的 Markdown
        if raw_response.startswith("```json"):
            raw_response = raw_response[7:]
        if raw_response.startswith("```"):
            raw_response = raw_response[3:]
        if raw_response.endswith("```"):
            raw_response = raw_response[:-3]
            
        data = json.loads(raw_response.strip())
        
        # 轉換出 normalized_value 供後續模擬使用；若發生格式錯誤則 ValueError 被 except 接走
        def get_norm_val(key):
            item = data.get(key, {})
            raw_val = item.get("raw_value")
            raw_un = item.get("raw_unit", "")
            if raw_val is None:
                raise ValueError(f"Missing raw_value for {key}")
            return normalize_parameter(raw_val, raw_un, key)

        params = {
            "transcription_rate": get_norm_val("transcription_rate"),
            "translation_rate": get_norm_val("translation_rate"),
            "mrna_deg_rate": get_norm_val("mrna_deg_rate"),
            "protein_deg_rate": get_norm_val("protein_deg_rate"),
            "kd": get_norm_val("kd"),
            "n": get_norm_val("n")
        }
        
    except Exception as e:
        # LLM 解析失敗或 API 錯誤時的保險機制 (Fallback)
        baseline = HOST_BASELINES.get(host_organism, HOST_BASELINES["Escherichia coli (大腸桿菌)"])
        params = baseline.copy()
        data = {
            "error": str(e),
            "notes": f"Data Miner Agent encountered an error. Falling back to safe hardcoded defaults for {host_organism} to prevent unhandled exceptions."
        }

    return {
        "params": params,
        "raw_response": data
    }

async def resolve_missing_parameters(missing_keys: list[str], context_parts: list[str], api_key: str | None, model_name: str, api_base: str | None = None, host_organism: str = "Escherichia coli (大腸桿菌)") -> dict:
    """
    非同步知識補全機制：
    當 UCF 第一層解析出缺漏的參數 (如 degradation rate) 時，呼叫此函式調用 LLM + 網路搜尋修補。
    如果失敗，利用 HOST_BASELINES 進行 Fallback 回退。
    """
    loop = asyncio.get_event_loop()
    def sync_miner():
        return mine_biochemical_data(context_parts, api_key, model_name, api_base, host_organism)
        
    res = await loop.run_in_executor(None, sync_miner)
    mined_params = res.get("params", {})
    
    baseline = HOST_BASELINES.get(host_organism, HOST_BASELINES["Escherichia coli (大腸桿菌)"])
    
    resolved = {}
    for key in missing_keys:
        if "protein_deg_rate" in key:
            resolved[key] = mined_params.get("protein_deg_rate", baseline["protein_deg_rate"])
        elif "mrna_deg_rate" in key:
            resolved[key] = mined_params.get("mrna_deg_rate", baseline["mrna_deg_rate"])
        elif "transcription_rate" in key:
            resolved[key] = mined_params.get("transcription_rate", baseline["transcription_rate"])
        elif "translation_rate" in key:
            resolved[key] = mined_params.get("translation_rate", baseline["translation_rate"])
        elif "kd" in key:
            resolved[key] = mined_params.get("kd", baseline["kd"])
        else:
            resolved[key] = mined_params.get("kd", baseline["kd"])
            
    return resolved

def parse_inducer_signal(signal_spec: str):
    """
    高自由度外部訊號產生器 (Dynamic Inducer Signal Generator):
    使用 sympy 解析數學方程式字串，並處理設定的起始時間。
    格式: "起始時間: 方程式" (例: "100: 500 * exp(-0.05 * (t - 100))")
    若不含冒號，則預設起始時間為 0。
    """
    if ":" in signal_spec:
        parts = signal_spec.split(":", 1)
        start_time = float(parts[0].strip())
        expr_str = parts[1].strip()
    else:
        start_time = 0.0
        expr_str = signal_spec.strip()
        
    t_sym = sp.Symbol('t')
    try:
        expr = sp.sympify(expr_str)
        # 將 symbolic function 轉為 numpy 可用的數值函數
        func = sp.lambdify(t_sym, expr, modules=["numpy", "math"])
        
        def signal_func(t_val):
            # 支援傳入 scalar 或 numpy array (繪圖時)
            if isinstance(t_val, np.ndarray):
                # 確保 func 回傳的是陣列
                val = func(t_val)
                if np.isscalar(val):
                    val = np.full_like(t_val, val, dtype=float)
                else:
                    val = np.array(val, dtype=float)
                val[t_val < start_time] = 0.0
                return val
            else:
                if t_val < start_time:
                    return 0.0
                return float(func(t_val))
                
        return signal_func
    except Exception as e:
        print(f"Error parsing inducer signal '{signal_spec}': {e}")
        # 若解析失敗，回傳全 0 函數
        return lambda t_val: np.zeros_like(t_val) if isinstance(t_val, np.ndarray) else 0.0


def circuit_dynamics(t, y, params, stimulus_funcs, topology):
    """
    動態核心 ODE 函數。
    確保時間變數 t 能實時影響外部輸入 X(t)。
    """
    species = topology.get("species", [])
    num_species = len(species)
    interactions = topology.get("interactions", [])
    inducers = topology.get("inducers", {})
    
    mRNAs = y[:num_species]
    proteins = y[num_species:]
    
    # 取得當前時刻的外部訊號濃度
    current_inducers = {ind_name: func(t) for ind_name, func in stimulus_funcs.items()}
        
    dy_dt = np.zeros(2 * num_species)
    
    # 解析壓抑項
    repressions = {s: [] for s in species}
    activations = {s: [] for s in species}
    for interaction in interactions:
        if interaction.get("type", "").lower() in ["repression", "repress"]:
            repressions[interaction["target"]].append(interaction["source"])
        elif interaction.get("type", "").lower() in ["activation", "activate"]:
            activations[interaction["target"]].append(interaction["source"])
    
    for i, target_species in enumerate(species):
        promoter_activity = 1.0
        
        # 使用 Hill Function 模擬多重抑制
        for repressor in repressions[target_species]:
            rep_idx = species.index(repressor)
            rep_conc = proteins[rep_idx]
            
            # 計算是否受到 Inducer 去活化
            active_rep_conc = rep_conc
            for ind_name, ind_data in inducers.items():
                if ind_data.get("target") == repressor:
                    I_conc = current_inducers.get(ind_name, 0.0)
                    Kd_ind = params.get(f"kd_inducer_{ind_name}", 5000.0)
                    n_ind = params.get(f"n_inducer_{ind_name}", 2.0)
                    active_rep_conc = rep_conc / (1.0 + (I_conc / Kd_ind)**n_ind)
            
            Kd_rep = params.get(f"kd_{repressor}", params.get("kd", 1000.0))
            n_rep = params.get(f"n_{repressor}", params.get("n", 2.0))
            promoter_activity *= 1.0 / (1.0 + (active_rep_conc / Kd_rep)**n_rep)
            
        # 處理活化項 (如果有的話)
        for activator in activations[target_species]:
            act_idx = species.index(activator)
            act_conc = proteins[act_idx]
            # 活化 Hill: (A/Kd)^n / (1 + (A/Kd)^n)
            Kd_act = params.get(f"kd_{activator}", params.get("kd", 1000.0))
            n_act = params.get(f"n_{activator}", params.get("n", 2.0))
            act_term = (act_conc / Kd_act)**n_act
            promoter_activity *= act_term / (1.0 + act_term)
        
        # 支援針對不同物種獨立取得參數，若無則 fallback 到通用參數
        leakage = params.get(f"leakage_{target_species}", params.get("leakage", 0.005))
        tx_rate = params.get(f"transcription_rate_{target_species}", params.get("transcription_rate", 8.33))
        mrna_deg = params.get(f"mrna_deg_rate_{target_species}", params.get("mrna_deg_rate", 0.00167))
        tl_rate = params.get(f"translation_rate_{target_species}", params.get("translation_rate", 0.033))
        prot_deg = params.get(f"protein_deg_rate_{target_species}", params.get("protein_deg_rate", 0.00083))
        
        effective_tx_rate = tx_rate * (leakage + (1.0 - leakage) * promoter_activity)
        
        dy_dt[i] = effective_tx_rate - mrna_deg * mRNAs[i]
        dy_dt[num_species + i] = tl_rate * mRNAs[i] - prot_deg * proteins[i]
        
    return dy_dt

def create_stimulus_func(curve_config):
    """將 curve_config 轉為 I(t) 函數"""
    if isinstance(curve_config, str):
        return parse_inducer_signal(curve_config)
    elif isinstance(curve_config, dict):
        ctype = curve_config.get("type", "pulse")
        if ctype == "pulse":
            start = curve_config.get("start", 0)
            duration = curve_config.get("duration", 1000)
            amp = curve_config.get("amp", 1.0)
            return lambda t: amp if start <= t <= (start + duration) else 0.0
        elif ctype == "sine":
            freq = curve_config.get("freq", 0.05)
            amp = curve_config.get("amp", 1.0)
            return lambda t: amp * (1 + np.sin(2 * np.pi * freq * t)) / 2
        else:
            return lambda t: 0.0
    return lambda t: 0.0

def run_ode_simulation(params: dict, topology: dict = None, stimulus_curves: dict = None):
    """
    動態 ODE 模擬引擎主函數：
    1. 接收自訂或預設的電路拓樸。
    2. 生成動態數學模型並利用 solve_ivp 積分 (處理 Stiff Equations)。
    3. 回傳 Pandas DataFrame 供外部繪製多變數圖表。
    """
    if topology is None:
        # 提供一個經典的 Repressilator (Oscillator) 作為展示
        topology = {
            "species": ["Lacl", "TetR", "cI"],
            "interactions": [
                {"source": "Lacl", "target": "TetR", "type": "repression"},
                {"source": "TetR", "target": "cI", "type": "repression"},
                {"source": "cI", "target": "Lacl", "type": "repression"} 
            ],
            "inducers": {
                "IPTG": {"target": "Lacl"}
            }
        }
        if stimulus_curves is None:
            stimulus_curves = {
                "IPTG": "20000: 50000 * exp(-0.0001 * (t - 20000))",
            }
            
    if stimulus_curves is None:
        stimulus_curves = topology.get("dynamic_inducers", {})

    stimulus_funcs = {}
    for ind_name, config in stimulus_curves.items():
        stimulus_funcs[ind_name] = create_stimulus_func(config)
        
    num_species = len(topology["species"])
    
    # 定義包裹給 solve_ivp 的函數 (符合 (t, y) 簽名)
    def rhs(t, y):
        return circuit_dynamics(t, y, params, stimulus_funcs, topology)
        
    # 模擬 100000 秒
    t_span = (0, 100000)
    t_eval = np.linspace(t_span[0], t_span[1], 5000)
    
    y0 = np.zeros(2 * num_species)
    # 打破對稱性以利觀察振盪或開關
    y0[num_species] = 5000.0 
    
    # 使用 Radau 或 BDF 方法以處理剛性 (Stiff) ODE，並設定防卡死參數
    sol = solve_ivp(
        fun=rhs, 
        t_span=t_span, 
        y0=y0, 
        method='Radau', 
        t_eval=t_eval,
        max_step=1000.0, # 最大步長限制避免跨幅過大
        rtol=1e-3, 
        atol=1e-6
    )
    
    # 將解轉為 DataFrame
    df_dict = {"Time": sol.t}
    for i, sp_name in enumerate(topology["species"]):
        df_dict[f"{sp_name}_mRNA"] = sol.y[i]
        df_dict[sp_name] = sol.y[num_species + i]
        
    for ind_name, func in stimulus_funcs.items():
        if isinstance(func(sol.t[0]), np.ndarray) or type(func).__name__ == 'function':
           arr = np.array([func(time_val) for time_val in sol.t])
           df_dict[f"Input_{ind_name}"] = arr
        else:
           df_dict[f"Input_{ind_name}"] = func(sol.t)
           
    df = pd.DataFrame(df_dict)
    
    return df

import concurrent.futures

def generate_noisy_parameters(base_params: dict, variance: float = 0.15, num_samples: int = 50) -> list[dict]:
    """為基礎參數疊加高斯噪音。若數值小於等於 0 則 Clip 到微小正數。"""
    samples = []
    for _ in range(num_samples):
        noisy_param = {}
        for k, v in base_params.items():
            if isinstance(v, (float, int)):
                if variance > 0.0:
                    # 簡單的高斯分佈擾動，平均 1.0, 變異 variance
                    noise_multiplier = np.random.normal(1.0, variance)
                    new_v = float(v) * noise_multiplier
                    # 數值確保為合理正值
                    noisy_param[k] = max(new_v, 1e-10)
                else:
                    noisy_param[k] = float(v)
            else:
                noisy_param[k] = v
        samples.append(noisy_param)
    return samples

def run_monte_carlo_ode_simulation(base_params: dict, topology: dict = None, stimulus_curves: dict = None, num_samples: int = 50, noise_level: float = 0.15) -> list[pd.DataFrame]:
    """平行執行多次不同微噪音參數的 ODE 模擬"""
    noisy_params_list = generate_noisy_parameters(base_params, variance=noise_level, num_samples=num_samples)
    
    results = []
    def sim_worker(p):
        try:
            return run_ode_simulation(p, topology, stimulus_curves)
        except Exception as e:
            print(f"Simulation failed for params: {e}")
            return pd.DataFrame()
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, num_samples)) as executor:
        # submit order doesn't matter since we just need 50 dfs
        futures = [executor.submit(sim_worker, p) for p in noisy_params_list]
        for f in concurrent.futures.as_completed(futures):
            res_df = f.result()
            if not res_df.empty:
                results.append(res_df)
                
    return results
