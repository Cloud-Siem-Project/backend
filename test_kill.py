import os
import time

def kill_worker(worker):

    with open(f"{worker}.pid") as f:
        pid = f.read().strip()

    print(f"[TEST] Killing {worker} (PID {pid})")

    os.system(f"taskkill /PID {pid} /F")


if __name__ == "__main__":

    print("[TEST] Waiting workers to start...")
    time.sleep(8)

    kill_worker("Worker-1")

    print("[TEST] Worker killed. Check master logs.")