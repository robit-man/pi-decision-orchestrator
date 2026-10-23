"""Conservative capability declarations; target verification is tracked separately."""

CATALOG = [
    {
        "name": "laya",
        "title": "Laya 421M",
        "description": "Compact typed decisions and classification",
        "download_mb": 844,
        "adapter": "decision",
        "capabilities": ["decision", "classification", "structured"],
        "source": "https://ollama.com/library/laya",
    },
    {
        "name": "tev1:0.8b",
        "title": "Tev1 0.8B",
        "description": "General decision routing with typed questions",
        "download_mb": 834,
        "adapter": "decision",
        "capabilities": ["decision", "classification", "structured"],
        "source": "https://ollama.com/library/tev1",
    },
    {
        "name": "d1-omni",
        "title": "D1-Omni 600M",
        "description": "Typed decisions with text, images, or short English audio",
        "download_mb": 850,
        "adapter": "decision",
        "capabilities": ["decision", "classification", "structured", "vision", "audio"],
        "source": "https://ollama.com/library/d1-omni",
    },
    {
        "name": "functiongemma",
        "title": "FunctionGemma 270M",
        "description": "Tool selection; does not expose native option probabilities",
        "download_mb": 301,
        "adapter": "tools",
        "capabilities": ["tools"],
        "source": "https://ollama.com/library/functiongemma",
    },
    {
        "name": "qwen3:0.6b",
        "title": "Qwen3 0.6B",
        "description": "Small structured chat fallback; no native option probabilities",
        "download_mb": 523,
        "adapter": "chat",
        "capabilities": ["chat", "tools", "structured"],
        "source": "https://ollama.com/library/qwen3:0.6b",
    },
]


def canonical(name: str) -> str:
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def catalog_entry(name: str) -> dict:
    return next(
        (dict(item) for item in CATALOG if canonical(item["name"]) == canonical(name)),
        {
            "name": name,
            "title": name,
            "description": "Additional local model; inspect its reported capabilities before use",
            "adapter": "chat",
            "capabilities": [],
            "download_mb": None,
        },
    )
