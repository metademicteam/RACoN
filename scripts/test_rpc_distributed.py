"""
Test script for Distributed RPC Layer Execution with Gemma-3-1B
Validates llama.cpp rpc-server + llama-cli distributed layer offloading.
"""
import subprocess
import time
import sys
import os

LLAMA_DIR = r"C:\Users\Haque\AppData\Local\Microsoft\WinGet\Packages\ggml.llamacpp_Microsoft.Winget.Source_8wekyb3d8bbwe"
RPC_SERVER = os.path.join(LLAMA_DIR, "rpc-server.exe")
LLAMA_CLI = os.path.join(LLAMA_DIR, "llama-cli.exe")
MODEL_PATH = r"F:\RACoN\Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf"

def main():
    print("=== RACoN Distributed Inference Test: Gemma-3-1B over RPC ===")
    print(f"Model: {MODEL_PATH}")
    print(f"Server binary: {RPC_SERVER}")
    print(f"Client binary: {LLAMA_CLI}")

    port = 50052
    print(f"\n[1/3] Starting rpc-server on 127.0.0.1:{port}...")
    server_proc = subprocess.Popen(
        [RPC_SERVER, "-H", "127.0.0.1", "-p", str(port), "-t", "4"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    try:
        time.sleep(2)
        if server_proc.poll() is not None:
            out, _ = server_proc.communicate()
            print(f"Error: rpc-server failed to start:\n{out}")
            return 1
        
        print("rpc-server running on 127.0.0.1:50052.")

        print(f"\n[2/3] Executing distributed token generation via llama-cli with --rpc 127.0.0.1:{port}...")
        prompt = "Compute 15 + 27 = "
        cmd = [
            LLAMA_CLI,
            "-m", MODEL_PATH,
            "--rpc", f"127.0.0.1:{port}",
            "-ngl", "26",
            "-p", prompt,
            "-n", "10",
            "--no-warmup",
            "--single-turn",
            "-t", "4"
        ]
        print(f"Running command: {' '.join(cmd)}")
        start_t = time.time()
        client_proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180
        )
        elapsed = time.time() - start_t

        print(f"\n[3/3] Client process finished in {elapsed:.2f}s (exit code {client_proc.returncode})")
        print("\n--- CLI STDERR (Backend initialization & layer allocation) ---")
        for line in client_proc.stderr.splitlines():
            if any(k in line.lower() for k in ["rpc", "device", "layer", "offload", "vram", "ram", "eval time", "tokens", "load", "model"]):
                print("  ", line)

        print("\n--- CLI STDOUT (Generated Output) ---")
        print(client_proc.stdout)

        return client_proc.returncode

    finally:
        print("\nShutting down rpc-server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("rpc-server stopped successfully.")

if __name__ == "__main__":
    sys.exit(main())
