"""测试支持工具（可选依赖，生产路径不导入）。"""

from app.testing.postgres import postgres_url

__all__ = ["postgres_url"]
