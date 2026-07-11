"""
Evaluator for online bin packing benchmark with subprocess execution and resource limits
"""
import importlib.util
import numpy as np
import time
import os
import signal
import subprocess
import tempfile
import traceback
import sys
import pickle
import resource


class TimeoutError(Exception):
    pass


class MemoryError(Exception):
    pass


def set_memory_limit(memory_limit_mb):
    """Set memory limit for the subprocess"""
    # Convert MB to bytes
    memory_limit_bytes = memory_limit_mb * 1024 * 1024
    
    # Set soft and hard limits
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))


def run_with_resource_limits(program_path, timeout_seconds=30, memory_limit_mb=512):
    """
    Run the program in a separate process with timeout and memory limits
    
    Args:
        program_path: Path to the program file
        timeout_seconds: Maximum execution time in seconds
        memory_limit_mb: Maximum memory usage in MB
    
    Returns:
        (items, bins_used, packing) tuple from the program
    """
    # Create a temporary file to execute
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as temp_file:
        # Write a script that executes the program and saves results
        script = f"""
import sys
import numpy as np
import os
import pickle
import traceback
import resource

# Set memory limit
def set_memory_limit_mb(mb):
    bytes_limit = mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))

# Set memory limit (in MB)
set_memory_limit_mb({memory_limit_mb})

# Add the directory to sys.path
sys.path.insert(0, os.path.dirname('{program_path}'))

# Debugging info
print(f"Running in subprocess, Python version: {{sys.version}}", file=sys.stderr)
print(f"Program path: {program_path}", file=sys.stderr)

try:
    # Import the program
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)
    
    # Generate deterministic instance (same seed as in the main evaluator)
    items = program.generate_instance(42)
    
    # Run the packing function
    print("Calling run_bin_packing()...", file=sys.stderr)
    items_result, bins_used, packing = program.run_bin_packing(42)
    print(f"run_bin_packing() returned successfully: bins_used = {{bins_used}}", file=sys.stderr)
    
    # Verify items match
    if len(items) != len(items_result):
        raise ValueError(f"Items length mismatch: {{len(items)}} != {{len(items_result)}}")
    
    for i, (a, b) in enumerate(zip(items, items_result)):
        if abs(a - b) > 1e-9:
            raise ValueError(f"Item {{i}} mismatch: {{a}} != {{b}}")
    
    # Save results to a file
    results = {{
        'items': items,
        'bins_used': bins_used,
        'packing': packing
    }}

    with open('{temp_file.name}.results', 'wb') as f:
        pickle.dump(results, f)
    print(f"Results saved to {temp_file.name}.results", file=sys.stderr)
    
except MemoryError as e:
    print(f"Memory limit exceeded: {{str(e)}}", file=sys.stderr)
    with open('{temp_file.name}.results', 'wb') as f:
        pickle.dump({{'error': 'MemoryLimitExceeded', 'message': str(e)}}, f)
except Exception as e:
    # If an error occurs, save the error instead
    print(f"Error in subprocess: {{str(e)}}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    with open('{temp_file.name}.results', 'wb') as f:
        pickle.dump({{'error': str(e)}}, f)
"""
        temp_file.write(script)
        temp_file_path = temp_file.name

    results_path = f"{temp_file_path}.results"

    try:
        # Run the script with timeout and resource limits
        process = subprocess.Popen(
            [sys.executable, temp_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=lambda: set_memory_limit(memory_limit_mb)
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            exit_code = process.returncode

            # Print output for debugging
            if stdout:
                print(f"Subprocess stdout: {stdout.decode()}")
            if stderr:
                print(f"Subprocess stderr: {stderr.decode()}")

            # Check for non-zero exit codes
            if exit_code != 0:
                raise RuntimeError(f"Process exited with code {exit_code}")

            # Load the results
            if os.path.exists(results_path):
                with open(results_path, "rb") as f:
                    results = pickle.load(f)

                # Check if an error was returned
                if "error" in results:
                    if results.get("error") == "MemoryLimitExceeded":
                        raise MemoryError(f"Program exceeded memory limit: {results.get('message', '')}")
                    else:
                        raise RuntimeError(f"Program execution failed: {results['error']}")

                return results["items"], results["bins_used"], results["packing"]
            else:
                raise RuntimeError("Results file not found")

        except subprocess.TimeoutExpired:
            # Kill the process if it times out
            process.kill()
            process.wait()
            stdout, stderr = process.communicate()
            raise TimeoutError(f"Process timed out after {timeout_seconds} seconds")
        
        except MemoryError as e:
            process.kill()
            process.wait()
            raise

    finally:
        # Clean up temporary files
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        if os.path.exists(results_path):
            os.unlink(results_path)


def evaluate_packing_solution(items, bins_used, packing, bin_capacity=1.0):
    """
    Validate a packing solution and compute metrics.
    
    Args:
        items: List of item sizes
        bins_used: Number of bins claimed to be used
        packing: List where packing[i] = bin index for item i
        bin_capacity: Capacity of each bin (default 1.0)
    
    Returns:
        Dictionary with validity flag and objective value
    """
    n_items = len(items)
    
    # Validate packing format
    if len(packing) != n_items:
        return {"valid": False, "objective": float("inf"), 
                "error": f"Packing length {len(packing)} != items length {n_items}"}
    
    # Check bin indices are non-negative integers
    if packing:
        max_bin = max(packing)
        if max_bin >= bins_used:
            return {"valid": False, "objective": float("inf"), 
                    "error": f"Bin index {max_bin} >= bins_used {bins_used}"}
        
        if min(packing) < 0:
            return {"valid": False, "objective": float("inf"), 
                    "error": f"Negative bin index {min(packing)}"}
    
    # Compute bin loads
    bin_loads = [0.0] * bins_used
    for i, (size, bin_idx) in enumerate(zip(items, packing)):
        bin_loads[bin_idx] += size
    
    # Check no bin exceeds capacity (with small tolerance for floating point)
    for b in range(bins_used):
        if bin_loads[b] > bin_capacity + 1e-9:
            return {"valid": False, "objective": float("inf"), 
                    "error": f"Bin {b} overloaded: {bin_loads[b]:.6f} > {bin_capacity}"}
    
    # Compute objective: lower bound is ceil(sum(items)/capacity)
    total_size = sum(items)
    lower_bound = np.ceil(total_size / bin_capacity)
    optimality_gap = (bins_used - lower_bound) / lower_bound if lower_bound > 0 else 0
    
    return {
        "valid": True,
        "objective": float(bins_used),
        "lower_bound": float(lower_bound),
        "optimality_gap": float(optimality_gap),
        "total_size": float(total_size),
        "bin_loads": bin_loads,
        "packing_density": float(total_size / (bins_used * bin_capacity)) if bins_used > 0 else 0.0
    }


def evaluate(program_path):
    """
    Evaluate the program by running it and checking the bin packing solution
    
    Args:
        program_path: Path to the program file
    
    Returns:
        Dictionary of metrics
    """
    try:
        start_time = time.time()
        
        # Use subprocess with resource limits
        items, bins_used, packing = run_with_resource_limits(
            program_path, 
            timeout_seconds=60,  # 1 minute timeout
            memory_limit_mb=512  # 512 MB memory limit
        )
        
        end_time = time.time()
        eval_time = end_time - start_time
        
        # Validate the solution
        validation = evaluate_packing_solution(items, bins_used, packing)
        
        if not validation["valid"]:
            print(f"Invalid packing: {validation['error']}")
            combined_score = 0.0
        else:
            # Compute combined score: lower is better (fewer bins)
            # Normalize by lower bound: score = 1 / (1 + optimality_gap)
            # This gives 1.0 for optimal, decreasing towards 0 as gap increases
            optimality_gap = validation["optimality_gap"]
            combined_score = 1.0 / (1.0 + optimality_gap) if optimality_gap >= 0 else 0.0
        
        print(f"Evaluation: valid={validation['valid']}, bins={bins_used}, "
              f"lower_bound={validation['lower_bound']}, gap={validation.get('optimality_gap', 0):.3f}, "
              f"score={combined_score:.3f}, time={eval_time:.2f}s")
        
        return {
            "bins_used": validation["objective"],
            "lower_bound": validation["lower_bound"],
            "optimality_gap": validation.get("optimality_gap", float("inf")),
            "validity": 1.0 if validation["valid"] else 0.0,
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
            "total_size": validation.get("total_size", 0.0),
            "packing_density": validation.get("packing_density", 0.0)
        }
        
    except TimeoutError as e:
        print(f"Evaluation timed out: {e}")
        return {
            "bins_used": float("inf"),
            "lower_bound": float("inf"),
            "optimality_gap": float("inf"),
            "validity": 0.0,
            "eval_time": 0.0,
            "combined_score": 0.0,
            "total_size": 0.0,
            "packing_density": 0.0,
            "error": "timeout"
        }
    
    except MemoryError as e:
        print(f"Evaluation exceeded memory limit: {e}")
        return {
            "bins_used": float("inf"),
            "lower_bound": float("inf"),
            "optimality_gap": float("inf"),
            "validity": 0.0,
            "eval_time": 0.0,
            "combined_score": 0.0,
            "total_size": 0.0,
            "packing_density": 0.0,
            "error": "memory_limit"
        }
    
    except Exception as e:
        print(f"Evaluation failed: {str(e)}")
        traceback.print_exc()
        return {
            "bins_used": float("inf"),
            "lower_bound": float("inf"),
            "optimality_gap": float("inf"),
            "validity": 0.0,
            "eval_time": 0.0,
            "combined_score": 0.0,
            "total_size": 0.0,
            "packing_density": 0.0,
            "error": str(e)
        }


def test_hostile_program():
    """
    Test that the evaluator can handle a hostile program (infinite loop)
    """
    # Create a hostile program that runs forever
    hostile_code = '''
import time
while True:
    time.sleep(1)
print("This should never be reached")
'''
    
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write(hostile_code)
        hostile_path = f.name
    
    try:
        # This should timeout
        result = evaluate(hostile_path)
        print(f"Hostile test result: {result}")
        return result.get("error") == "timeout"
    finally:
        os.unlink(hostile_path)


# Stage-based evaluation for cascade evaluation
def evaluate_stage1(program_path):
    """
    First stage evaluation - quick validation check
    """
    return evaluate(program_path)


def evaluate_stage2(program_path):
    """
    Second stage evaluation - full evaluation (same as main for now)
    """
    return evaluate(program_path)


if __name__ == "__main__":
    # Test the evaluator
    print("Testing evaluator with a simple program...")
    
    # Create a simple test program
    test_program = '''
import numpy as np

def generate_instance(seed=42):
    rng = np.random.RandomState(seed)
    return [rng.uniform(0.1, 0.3) for _ in range(10)]

def run_bin_packing(seed=42):
    items = generate_instance(seed)
    bins_used = 5
    packing = [i % 5 for i in range(10)]
    return items, bins_used, packing
'''
    
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
        f.write(test_program)
        test_path = f.name
    
    try:
        result = evaluate(test_path)
        print(f"Test result: {result}")
        
        # Test hostile program handling
        print("\nTesting hostile program handling...")
        hostile_handled = test_hostile_program()
        print(f"Hostile program correctly timed out: {hostile_handled}")
        
    finally:
        os.unlink(test_path)