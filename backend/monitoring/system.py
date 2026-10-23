import asyncio
import os
import platform
import struct
import time
from pathlib import Path

import psutil

from backend.errors import DomainError


class Monitor:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.latest = {}
        self.process = psutil.Process()
        psutil.cpu_percent()

    def sample(self) -> dict:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self.settings.data_dir)
        temperature = None
        try:
            temperature = float(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        except (OSError, ValueError):
            pass
        throttled = None
        try:
            throttled = Path("/sys/devices/platform/soc/soc:firmware/get_throttled").read_text().strip()
        except OSError:
            pass
        alerts = []
        if memory.available < self.settings.min_available_mb * 1024**2:
            alerts.append(
                {"code": "memory_pressure", "message": "Available RAM is below the inference reserve."}
            )
        if temperature is not None and temperature >= self.settings.max_temperature:
            alerts.append({"code": "thermal_pressure", "message": "The CPU is too hot for a new inference."})
        if disk.free < 512 * 1024**2:
            alerts.append({"code": "disk_pressure", "message": "Less than 512 MB of disk space remains."})
        self.latest = {
            "hostname": platform.node(),
            "architecture": platform.machine(),
            "userspace_bits": struct.calcsize("P") * 8,
            "cpu_percent": psutil.cpu_percent(),
            "cpu_count": os.cpu_count(),
            "temperature_c": temperature,
            "throttled": throttled,
            "uptime_s": time.time() - psutil.boot_time(),
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "percent": memory.percent,
            },
            "disk": {"total": disk.total, "free": disk.free, "used": disk.used, "percent": disk.percent},
            "app_rss_bytes": self.process.memory_info().rss,
            "alerts": alerts,
            "sampled_at": time.time(),
        }
        return self.latest

    def permit(self, model_bytes=0):
        status = self.sample()
        if self.settings.testing:
            return
        for alert in status["alerts"]:
            if alert["code"] in {"memory_pressure", "thermal_pressure", "disk_pressure"}:
                raise DomainError(alert["code"], alert["message"], 503)
        if (
            model_bytes
            and model_bytes > status["memory"]["available"] - self.settings.min_available_mb * 1024**2
        ):
            raise DomainError(
                "model_memory_risk",
                "Model weights exceed available RAM after the inference reserve. Unload other models first.",
                409,
            )

    async def run(self):
        count = 0
        while True:
            self.sample()
            count += 1
            if count % 100 == 0:
                self.store.prune(int(self.store.setting("history_limit", self.settings.history_limit)))
            await asyncio.sleep(self.settings.telemetry_seconds)
