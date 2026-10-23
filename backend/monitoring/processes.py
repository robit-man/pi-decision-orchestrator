import psutil


def ollama_rss():
    """Sum the server and its descendants, including runners with different names."""
    processes = {}
    for process in psutil.process_iter(["pid", "ppid", "name", "memory_info"]):
        try:
            processes[process.pid] = process.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    selected = {pid for pid, info in processes.items() if "ollama" in (info["name"] or "").lower()}
    while True:
        children = {pid for pid, info in processes.items() if info["ppid"] in selected}
        if children <= selected:
            break
        selected.update(children)
    return sum(processes[pid]["memory_info"].rss for pid in selected if processes[pid]["memory_info"])
