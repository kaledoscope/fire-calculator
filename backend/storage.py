"""storage.py —— 存储抽象层（SRS §5.3）。

宪法铁律 Ⅱ：上层（engine / main）**不感知数据存在哪**。
现在落 JSON 文件，第 6 期换 PostgreSQL —— 只换实现，不动调用方。

新增一种存储 = 新增一个 Storage 子类 + 改一处 `get_storage()`，
不修改任何已有代码。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from backend.models import Config

# 配置名只允许字母、数字、下划线、连字符、中文。
# 这是**安全边界**：不加限制的话 `../../etc/passwd` 就是一个路径穿越漏洞。
_SAFE_NAME = re.compile(r"^[\w一-鿿-]{1,64}$", re.UNICODE)


class StorageError(Exception):
    """存储层错误。上层统一按此捕获，不关心底层是文件还是数据库。"""


class InvalidConfigName(StorageError):
    pass


class ConfigNotFound(StorageError):
    pass


class Storage(ABC):
    """存储契约。所有实现都必须满足这五个方法。"""

    @abstractmethod
    def save_config(self, name: str, config: Config) -> None: ...

    @abstractmethod
    def load_config(self, name: str) -> Config | None: ...

    @abstractmethod
    def list_configs(self) -> list[dict[str, str]]: ...

    @abstractmethod
    def delete_config(self, name: str) -> None: ...

    @abstractmethod
    def config_exists(self, name: str) -> bool: ...


def _validate_name(name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise InvalidConfigName(
            "配置名只能用中英文、数字、下划线和连字符，长度 1~64"
        )
    return name


class JsonFileStorage(Storage):
    """第 1~5 期的实现：一个配置一个 JSON 文件。

    为什么先落文件而不是直接上 PostgreSQL：
    期 1 的目标是「浏览器里能填、能算、能看到数」。数据库的建库、迁移、
    连接配置会全部压在这一步之前，而它们对「看到数」毫无帮助。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self._base_dir / f"{_validate_name(name)}.json"

    def save_config(self, name: str, config: Config) -> None:
        path = self._path(name)
        payload = {
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "config": config.model_dump(mode="json"),
        }
        # 先写临时文件再原子替换 —— 中途失败不会留下半个配置文件
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(path)

    def load_config(self, name: str) -> Config | None:
        path = self._path(name)
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        try:
            return Config.model_validate_json(_extract_config(raw))
        except Exception as exc:  # noqa: BLE001 —— 统一转成存储层错误
            raise StorageError(f"配置「{name}」无法解析：{exc}") from exc

    def list_configs(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for path in sorted(self._base_dir.glob("*.json")):
            stat = path.stat()
            items.append(
                {
                    "name": path.stem,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return items

    def delete_config(self, name: str) -> None:
        path = self._path(name)
        if not path.exists():
            raise ConfigNotFound(f"配置「{name}」不存在")
        path.unlink()

    def config_exists(self, name: str) -> bool:
        return self._path(name).exists()


def _extract_config(raw: str) -> str:
    """从存储信封里取出纯 config 的 JSON 文本。

    信封结构 `{name, saved_at, schema_version, config}` 是存储层的私事，
    上层拿到的永远只是 Config —— 换存储介质时信封可以整个换掉。
    """
    payload = json.loads(raw)
    return json.dumps(payload.get("config", payload), ensure_ascii=False)


# ── 工厂 ────────────────────────────────────────────────────────────

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "configs"
_storage: Storage | None = None


def get_storage() -> Storage:
    """返回当前存储实现。

    第 6 期换成 PostgreSQL 时，只改这一个函数。
    """
    global _storage
    if _storage is None:
        _storage = JsonFileStorage(_DEFAULT_DIR)
    return _storage
