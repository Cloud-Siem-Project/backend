import multiprocessing
import time
import os
#taskkill /PID 15432 /F                worker kill command in 2nd bash

def worker(name, queue):

    pid = os.getpid()

    with open(f"{name}.pid", "w") as f:
        f.write(str(pid))

    while True:
        queue.put({
            "worker": name,
            "timestamp": time.time()
        })

        print(f"{name} sent heartbeat (PID {pid})")

        time.sleep(3)

def start_worker(name, queue):
    process = multiprocessing.Process(target=worker, args=(name, queue))
    process.start()
    return process


def master():
    queue = multiprocessing.Queue()

    workers = {
        "Worker-1": start_worker("Worker-1", queue),
        "Worker-2": start_worker("Worker-2", queue)
    }

    last_seen = {}

    while True:
        # Heartbeat al
        while not queue.empty():
            msg = queue.get()
            last_seen[msg["worker"]] = msg["timestamp"]

        current_time = time.time()

        for name, process in list(workers.items()):
            # Eğer process öldüyse
            if not process.is_alive():
                print(f"⚠️ {name} crashed! Restarting...")
                workers[name] = start_worker(name, queue)

            # Heartbeat kontrolü
            elif name in last_seen and current_time - last_seen[name] > 6:
                print(f"⚠️ {name} not responding! Restarting...")
                process.terminate()
                workers[name] = start_worker(name, queue)

        time.sleep(1)


if __name__ == "__main__":
    master()
    