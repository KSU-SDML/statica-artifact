"""
=========================================================================================
STATICA EVALUATION METRICS GUIDE
=========================================================================================
In the context of Statica, we are acting as a binary classifier evaluating C# methods:
Positive = "This method is safe to make static."
Negative = "This method requires an instance context (do not make static)."

--- THE CORE METRICS ---
1.  True Positives (TP)
    Statica approved the method -> AND -> The CA1822 Baseline agrees.
    * Occurs in:
     - Stage 1: Direct Analysis
     - Stage 3: Cascading Analysis
    * Note: The LLM stage cannot generate TPs because it acts purely as a negative filter.

2.  False Positives (FP)
    Statica approved the method -> BUT -> The CA1822 Baseline says it requires an instance.
    * Occurs in:
     - Stage 1: Direct Analysis
     - Stage 3: Cascading Analysis
   * Note: The LLM stage cannot generate FPs. It only has the power to reject.

3.  False Negatives (FN)
    Statica rejected the method -> BUT -> The CA1822 Baseline says it is actually safe.
    * Occurs in:
     - Stage 1: Direct Analysis (Deterministic rules failed)
     - Stage 2: LLM Analysis (Model did not solve the unknown parents problem)
     - Stage 3: Cascading Analysis (Failed to resolve the call dependency chain)
     - Global: "Ghost methods" that failed to parse initially but are in the baseline.

--- WHY TRUE NEGATIVES (TN) ARE OMITTED ---
True Negatives (TN) are instance methods that Statica correctly rejected. 
Because >90% of methods naturally require instance state, the TN class is massive. 
Using TN to calculate "Accuracy" ((TP + TN) / Total) is misleading because a tool 
that rejects 100% of methods would still score ~90% Accuracy. We evaluate purely 
on finding the safe static methods (Positive Class) using the equations below.

--- PERFORMANCE EQUATIONS ---

* Precision (The "Trust" Metric):
  Out of all methods we flagged as static, how many were actually correct?
  Precision = TP / (TP + FP)

* Recall (The "Reach" Metric):
  Out of all truly static methods hidden in the codebase, what percentage did we find?
  Recall = TP / (TP + FN)

* F1-Score (The "Balance" Metric):
  The harmonic mean of Precision and Recall. Proves the tool is both safe and aggressive.
  F1-Score = 2 * (Precision * Recall) / (Precision + Recall)
=========================================================================================
"""

import csv
import os
import math

def calculate_stats(data_list):
    """Calculates Mean and Sample Standard Deviation (n-1)"""
    n = len(data_list)
    if n == 0: return 0.0, 0.0
    mean = sum(data_list) / n
    if n == 1: return mean, 0.0 
    variance = sum((x - mean) ** 2 for x in data_list) / (n - 1)
    return mean, math.sqrt(variance)

def evaluate_pipeline(run_files, direct_baseline_file, cascading_baseline_file, additional_baseline_file, broken_baseline_file, system_name, export_dir):
    # 1. Load the Golden Truths (Baselines)
    golden_truth_direct = {}
    x_fieldnames = None
    
    with open(direct_baseline_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        x_fieldnames = reader.fieldnames
        for row in reader:
            key = (row['Method'], row['Line'], row['FilePath'])
            golden_truth_direct[key] = row
            
    golden_truth_cascading = {}
    with open(cascading_baseline_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['Method'], row['Line'], row['FilePath'])
            golden_truth_cascading[key] = row

    # Load the manually verified additional candidates
    golden_truth_additional = {}
    with open(additional_baseline_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['method_name'], row['line_number'], row['file_name'])
            golden_truth_additional[key] = row

    # Load the broken srcML candidates
    broken_keys = set()
    with open(broken_baseline_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row['method_name'], row['line_number'], row['file_name'])
            broken_keys.add(key)
                    
    if broken_keys:
        print(f"Loaded {len(broken_keys)} broken srcML items to EXCLUDE from evaluation metrics.")

    print(f"Loaded {len(golden_truth_direct)} direct golden truth items.")
    print(f"Loaded {len(golden_truth_cascading)} cascading golden truth items.")
    print(f"Loaded {len(golden_truth_additional)} manually verified additional golden truth items.\n")

    # Tracking TP, FP, and FN for every stage
    all_runs_metrics = {
        'direct_static': [], 'direct_non_static': [], 'direct_tp': [], 'direct_fp': [], 'direct_fn': [],
        'llm_passed': [], 'llm_non_static': [], 'llm_fn': [], 
        'casc_static': [], 'casc_non_static': [], 'casc_tp': [], 'casc_fp': [], 'casc_fn': []
    }

    perf_metrics = {'tp': [], 'fp': [], 'fn': [], 'precision': [], 'recall': [], 'f1': []}
    
    # Track aggregated coverage sizes
    coverage_metrics = {'cov_direct': [], 'cov_cascading': [], 'cov_additional': [], 'true_fps': []}

    # 2. Process all runs
    for run_file in run_files:
        run_name = os.path.splitext(os.path.basename(run_file))[0]
        metrics = {k: 0 for k in all_runs_metrics.keys()}
        
        covered_direct_rows, covered_cascading_rows, covered_additional_rows, leftover_rows = [], [], [], []
        rejected_direct_rows, rejected_llm_rows, rejected_cascading_rows = [], [], []
        matched_direct_keys, matched_cascading_keys, matched_additional_keys = set(), set(), set()
        y_fieldnames = None
        
        with open(run_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            y_fieldnames = reader.fieldnames
            
            # --- STEP 1 & 2: Front-Door Exclusion ---
            valid_rows = []
            for row in reader:
                key = (row['method_name'], row['line_number'], row['file_name'])
                if key not in broken_keys:
                    valid_rows.append(row)
            
            # --- STEP 3: Print Valid Input Size ---
            input_size = len(valid_rows)
            print(f"Run {run_name} | Total Input Size (excluding broken): {input_size}")

            # --- STEP 4: Process the remaining valid logic ---
            for row in valid_rows:
                key = (row['method_name'], row['line_number'], row['file_name'])

                is_static = row['to_static'] == 'True'
                reasoning = row['reasoning']

                is_direct = reasoning == 'Direct Analysis'
                is_pure_cascading = reasoning == 'Cascading Analysis'
                is_llm_no_calls = reasoning == 'Cascading + LLM (No Internal Calls)'
                is_llm_with_calls = reasoning == 'Cascading + LLM (With Internal Calls)'
                is_from_llm = not (is_direct or is_pure_cascading)
                is_llm_rejected = not (is_direct or is_pure_cascading or is_llm_no_calls or is_llm_with_calls)

                # --- Flow Counting ---
                if is_direct:
                    if is_static: metrics['direct_static'] += 1
                    else: metrics['direct_non_static'] += 1

                if is_from_llm:
                    if is_llm_rejected: metrics['llm_non_static'] += 1
                    else: metrics['llm_passed'] += 1
                        
                if is_pure_cascading or is_llm_with_calls or is_llm_no_calls:
                    if is_static: metrics['casc_static'] += 1
                    else: metrics['casc_non_static'] += 1

                # --- Performance Metrics Tracking ---
                # Positive matches checked against a compilation-dependent analyzer (Microsoft Roslyn) baseline
                is_tp = is_static and (key in golden_truth_direct or key in golden_truth_cascading or key in golden_truth_additional)
                is_fn = not is_static and (key in golden_truth_direct or key in golden_truth_cascading or key in golden_truth_additional)
                is_fp = is_static and (key not in golden_truth_direct) and (key not in golden_truth_cascading) and (key not in golden_truth_additional)

                if is_direct:
                    if is_static:
                        if is_tp: metrics['direct_tp'] += 1
                        if is_fp: metrics['direct_fp'] += 1
                    else:
                        if is_fn: metrics['direct_fn'] += 1

                if is_from_llm:
                    if is_llm_rejected:
                        if is_fn: metrics['llm_fn'] += 1
                        
                if is_pure_cascading or is_llm_with_calls or is_llm_no_calls:
                    if is_static:
                        if is_tp: metrics['casc_tp'] += 1
                        if is_fp: metrics['casc_fp'] += 1
                    else:
                        if is_fn: metrics['casc_fn'] += 1
                        
                # --- Detailed Data Extraction ---
                if is_static:
                    if key in golden_truth_direct:
                        covered_direct_rows.append(row)
                        matched_direct_keys.add(key)
                    elif key in golden_truth_cascading:
                        covered_cascading_rows.append(row)
                        matched_cascading_keys.add(key)
                    elif key in golden_truth_additional:
                        covered_additional_rows.append(row)
                        matched_additional_keys.add(key)
                    else:
                        leftover_rows.append(row)
                else:
                    if is_direct: rejected_direct_rows.append(row)
                    elif is_llm_rejected: rejected_llm_rows.append(row)
                    else: rejected_cascading_rows.append(row)

        for k in metrics.keys():
            all_runs_metrics[k].append(metrics[k])
            
        # --- Calculate Performance Metrics ---
        run_tp = metrics['direct_tp'] + metrics['casc_tp']
        run_fp = metrics['direct_fp'] + metrics['casc_fp']
        run_fn = metrics['direct_fn'] + metrics['llm_fn'] + metrics['casc_fn']
        
        run_precision = (run_tp / (run_tp + run_fp)) * 100 if (run_tp + run_fp) > 0 else 0
        run_recall = (run_tp / (run_tp + run_fn)) * 100 if (run_tp + run_fn) > 0 else 0
        run_f1 = (2 * run_precision * run_recall) / (run_precision + run_recall) if (run_precision + run_recall) > 0 else 0

        perf_metrics['tp'].append(run_tp)
        perf_metrics['fp'].append(run_fp)
        perf_metrics['fn'].append(run_fn)
        perf_metrics['precision'].append(run_precision)
        perf_metrics['recall'].append(run_recall)
        perf_metrics['f1'].append(run_f1)
            
        coverage_metrics['cov_direct'].append(len(covered_direct_rows))
        coverage_metrics['cov_cascading'].append(len(covered_cascading_rows))
        if golden_truth_additional:
            coverage_metrics['cov_additional'].append(len(covered_additional_rows))
        coverage_metrics['true_fps'].append(len(leftover_rows))
            
        # --- PREPARE MASTER TP, FP, FN LISTS ---
        master_tp_rows = covered_direct_rows + covered_cascading_rows + covered_additional_rows
        master_fn_rows = [row for k, row in golden_truth_direct.items() if k not in matched_direct_keys] + \
                         [row for k, row in golden_truth_cascading.items() if k not in matched_cascading_keys] + \
                         [row for k, row in golden_truth_additional.items() if k not in matched_additional_keys]
        master_fp_rows = leftover_rows
        
        # --- EXPORT TO CSV ---
        run_export_dir = os.path.join(export_dir, "results", run_name)
        os.makedirs(run_export_dir, exist_ok=True)
        
        def write_csv(filename, fieldnames, rows):
            path = os.path.join(run_export_dir, filename)
            with open(path, 'w', encoding='utf-8', newline='') as fw:
                writer = csv.DictWriter(fw, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        
        write_csv("statica_TP.csv", y_fieldnames, master_tp_rows)
        write_csv("statica_FP.csv", y_fieldnames, master_fp_rows)
        write_csv("statica_FN.csv", x_fieldnames, master_fn_rows)
            
        write_csv("statica_rejected_direct.csv", y_fieldnames, rejected_direct_rows)
        write_csv("statica_rejected_llm.csv", y_fieldnames, rejected_llm_rows)
        write_csv("statica_rejected_cascading.csv", y_fieldnames, rejected_cascading_rows)

    # 3. Format Output
    final_stats = {}
    for key, data_list in all_runs_metrics.items():
        if key in ['llm_fp', 'llm_tp']: continue
        mean, std = calculate_stats(data_list)
        rounded_mean = int(round(mean))
        rounded_std = int(round(std))
        final_stats[key] = f"{rounded_mean}" if rounded_std == 0 else f"{rounded_mean} ± {rounded_std}"
            
    dir_in = [s + ns for s, ns in zip(all_runs_metrics['direct_static'], all_runs_metrics['direct_non_static'])]
    llm_in = [s + ns for s, ns in zip(all_runs_metrics['llm_passed'], all_runs_metrics['llm_non_static'])]
    casc_in = [s + ns for s, ns in zip(all_runs_metrics['casc_static'], all_runs_metrics['casc_non_static'])]
    
    for label, data in [('direct_input', dir_in), ('llm_input', llm_in), ('casc_input', casc_in)]:
        m, s = calculate_stats(data)
        rm, rs = int(round(m)), int(round(s))
        final_stats[label] = f"{rm}" if rs == 0 else f"{rm} ± {rs}"

    perf_stats = {}
    for key, data_list in perf_metrics.items():
        mean, std = calculate_stats(data_list)
        if key in ['tp', 'fp', 'fn']:
            rm, rs = int(round(mean)), int(round(std))
            perf_stats[key] = f"{rm}" if rs == 0 else f"{rm} ± {rs}"
        else: 
            perf_stats[key] = f"{mean:.1f}%" if std == 0 else f"{mean:.1f}% ± {std:.1f}%"
            
    print("\n" + "="*100)
    print(" REFERENCE SET COVERAGE (Aggregate)")
    print("="*100)
    for key, label in [('cov_direct', 'Statica Coverage (Direct)'), 
                       ('cov_cascading', 'Statica Coverage (Cascading)'), 
                       ('cov_additional', 'Statica Coverage (Additional)'),
                       ('true_fps', 'Statica Additional (True FPs)')]:
        if coverage_metrics.get(key):
            m, s = calculate_stats(coverage_metrics[key])
            rm, rs = int(round(m)), int(round(s))
            formatted_val = f"{rm}" if rs == 0 else f"{rm} ± {rs}"
            print(f"  {label:<30}: {formatted_val}")

    print("\n" + "="*100)
    print(" TABLE VI: PIPELINE DATA FLOW (Aggregate)")
    print("="*100)
    print("Direct Analysis:")
    print(f"  Evaluated:  {final_stats['direct_input']}")
    print(f"  Approved:   {final_stats['direct_static']}")
    print(f"  Rejected:   {final_stats['direct_non_static']}\n")
    
    print("LLM Analysis (Gateway) (μ ± σ):")
    print(f"  Evaluated:  {final_stats['llm_input']}")
    print(f"  Passed:     {final_stats.get('llm_passed', '0')}")
    print(f"  Rejected:   {final_stats['llm_non_static']}\n")
    
    print("Cascading Analysis (μ ± σ):")
    print(f"  Evaluated:  {final_stats['casc_input']}")
    print(f"  Approved:   {final_stats['casc_static']}")
    print(f"  Rejected:   {final_stats['casc_non_static']}")
    
    print("\n" + "="*100)
    print(f" TABLE VII: PERFORMANCE METRICS ({system_name}) ")
    print("="*100)
    print(f"  True Positives (TP):  {perf_stats['tp']}")
    print(f"  False Positives (FP): {perf_stats['fp']}")
    print(f"  False Negatives (FN): {perf_stats['fn']}")
    print(f"  Precision:            {perf_stats['precision']}")
    print(f"  Recall:               {perf_stats['recall']}")
    print(f"  F1-Score:             {perf_stats['f1']}")
    print("="*100)

if __name__ == "__main__":
    SYSTEM_NAME = "Files-4.0.24"
    BASE_DIR = f"/home/ali/Statica/systems/{SYSTEM_NAME}"
    
    RUN_FILES = [
        f"{BASE_DIR}/results/1.csv",
        f"{BASE_DIR}/results/2.csv",
        f"{BASE_DIR}/results/3.csv",
        f"{BASE_DIR}/results/4.csv",
        f"{BASE_DIR}/results/5.csv",
    ]
    
    # Use the *_Only_Covered.csv
    DIRECT_BASELINE = f"{BASE_DIR}/CA1822/CA1822_Direct_Candidates_Filtered_Methods_Only_Covered.csv"
    CASCADING_BASELINE = f"{BASE_DIR}/CA1822/CA1822_Cascading_Candidates_Filtered_Methods_Only.csv"
    ADDITIONAL_BASELINE = f"{BASE_DIR}/results/additional_candidates.csv"
    BROKEN_BASELINE = f"{BASE_DIR}/results/candidates_broken.csv"

    evaluate_pipeline(
        run_files=RUN_FILES, 
        direct_baseline_file=DIRECT_BASELINE, 
        cascading_baseline_file=CASCADING_BASELINE, 
        additional_baseline_file=ADDITIONAL_BASELINE,
        broken_baseline_file=BROKEN_BASELINE,
        system_name=SYSTEM_NAME, 
        export_dir=BASE_DIR
    )