"""配置管理 - 支持环境变量、配置文件、命令行参数的优先级读取"""

import os
from pathlib import Path
from typing import Optional


def get_config_dir() -> Path:
    """获取配置目录"""
    config_dir = Path.home() / ".dj-set-curator"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    """获取配置文件路径"""
    return get_config_dir() / "config.yaml"


def load_config() -> dict:
    """加载配置文件，返回配置字典"""
    config_path = get_config_path()
    if not config_path.exists():
        return {}

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # yaml 未安装，尝试用 JSON
        try:
            import json
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}
    except Exception:
        return {}


def save_config(config: dict) -> bool:
    """保存配置到文件"""
    config_path = get_config_path()
    try:
        import yaml
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        return True
    except ImportError:
        try:
            import json
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False
    except Exception:
        return False


def get_mcp_server_command(cli_value: Optional[str] = None) -> str:
    """
    按优先级获取 MCP server 命令：
    1. 命令行参数 --server
    2. 环境变量 DJ_CURATOR_MCP_SERVER
    3. 配置文件 mcp_server_command
    4. 默认 "cloud-music-mcp"
    """
    if cli_value and cli_value != "cloud-music-mcp":
        return cli_value

    if env_value := os.environ.get("DJ_CURATOR_MCP_SERVER"):
        return env_value

    config = load_config()
    if config_value := config.get("mcp_server_command"):
        return config_value

    return "cloud-music-mcp"
