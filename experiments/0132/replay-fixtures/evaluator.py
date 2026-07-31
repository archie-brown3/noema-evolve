import re


def evaluate(program_path):
    code = open(program_path, encoding="utf-8").read()
    if "RAISE_EVAL = True" in code:
        raise RuntimeError("fixture evaluator failure")
    match = re.search(r"^VALUE\s*=\s*(-?\d+(?:\.\d+)?)\s*$", code, re.MULTILINE)
    if match is None:
        return {"error": 1.0}
    value = float(match.group(1))
    return {"combined_score": value / 10.0, "value": value}
