import yaml
import json
import traceback
import os

with open("tests/eval/eval_config.yaml", "r") as f:
    config = yaml.safe_load(f)

with open("artifacts/traces/generated_traces.json", "r") as f:
    traces = json.load(f)

custom_metrics = {m["name"]: m["custom_function"] for m in config.get("custom_metrics", [])}

for name, code in custom_metrics.items():
    print(f"--- Executing custom function for metric: {name} ---")
    local_vars = {}
    try:
        # Compile and execute the function definition code
        exec(code, globals(), local_vars)
        evaluate_func = local_vars.get("evaluate")
        if not evaluate_func:
            print(f"Error: Function 'evaluate' not found in metric {name}")
            continue
            
        for case in traces["eval_cases"]:
            print(f"  Testing case: {case['eval_case_id']}")
            try:
                res = evaluate_func(case)
                print(f"    Result: {res}")
            except Exception as ex:
                print(f"    Failed:")
                traceback.print_exc()
    except Exception as e:
        print(f"  Failed to compile/execute function definition:")
        traceback.print_exc()
