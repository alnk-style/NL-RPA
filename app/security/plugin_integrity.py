"""插件（MCP Server）版本、指纹与可选签名校验。

指纹是对插件配置的规范化摘要：传输方式、地址/命令、参数、环境变量
与请求头（值参与摘要，但密钥只以密文存储，验证时临时解密）。启用
``PLUGIN_INTEGRITY`` 后：

- 未填写版本或指纹、指纹不匹配的插件直接拒绝加载（不发起网络调用）；
- ``PLUGIN_REQUIRE_SIGNATURE=true`` 时还需要 HMAC-SHA256 签名，
  密钥来自 ``PLUGIN_SIGNING_SECRET``；
- 工具清单变化时由注册表重置授权，要求重新确认。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

FINGERPRINT_PREFIX = "sha256:"
SIGNATURE_PREFIX = "hmac-sha256:"


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    reason: str
    fingerprint: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
        }


def canonical_plugin_spec(
    *,
    transport: str,
    url: str | None,
    command: str | None,
    args: list[Any] | None,
    env: dict[str, Any] | None,
    headers: dict[str, Any] | None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    """生成与存储顺序无关的规范化配置字典。"""

    return {
        "transport": str(transport or "http").strip().lower(),
        "url": (str(url).strip() if url else None),
        "command": (str(command).strip() if command else None),
        "args": [str(item) for item in (args or [])],
        "env": {
            str(key): str(value)
            for key, value in sorted((env or {}).items())
        },
        "headers": {
            str(key): str(value)
            for key, value in sorted((headers or {}).items())
        },
        "has_secret": bool(auth_token),
        "secret_sha256": (
            hashlib.sha256(str(auth_token).encode("utf-8")).hexdigest()
            if auth_token
            else None
        ),
    }


def compute_fingerprint(spec: dict[str, Any]) -> str:
    payload = json.dumps(
        spec,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def compute_signature(fingerprint: str, secret: str) -> str:
    digest = hmac.new(
        str(secret).encode("utf-8"),
        str(fingerprint).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(
    fingerprint: str,
    signature: str | None,
    secret: str | None,
) -> bool:
    if not signature or not secret:
        return False
    expected = compute_signature(fingerprint, secret)
    return hmac.compare_digest(str(signature), expected)


def compute_toolset_fingerprint(tools: Any) -> str:
    normalized: list[dict[str, Any]] = []
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": str(item.get("name") or ""),
                "input_schema": item.get("input_schema")
                or item.get("inputSchema")
                or {},
            }
        )
    normalized.sort(key=lambda item: item["name"])
    return compute_fingerprint({"tools": normalized})


def verify_plugin_config(
    *,
    transport: str,
    url: str | None,
    command: str | None,
    args: list[Any] | None,
    env: dict[str, Any] | None,
    headers: dict[str, Any] | None,
    auth_token: str | None,
    version: str | None,
    fingerprint: str | None,
    signature: str | None,
    enforce: bool,
    require_signature: bool = False,
    signing_secret: str | None = None,
) -> IntegrityResult:
    computed = compute_fingerprint(
        canonical_plugin_spec(
            transport=transport,
            url=url,
            command=command,
            args=args,
            env=env,
            headers=headers,
            auth_token=auth_token,
        )
    )
    if not enforce:
        return IntegrityResult(True, "integrity_disabled", computed)
    if not str(version or "").strip():
        return IntegrityResult(False, "插件缺少版本号", computed)
    if not str(fingerprint or "").strip():
        return IntegrityResult(False, "插件缺少指纹", computed)
    if str(fingerprint).strip() != computed:
        return IntegrityResult(False, "插件指纹不匹配", computed)
    if require_signature and not verify_signature(
        computed,
        signature,
        signing_secret,
    ):
        return IntegrityResult(False, "插件签名缺失或不匹配", computed)
    return IntegrityResult(True, "ok", computed)


__all__ = [
    "FINGERPRINT_PREFIX",
    "SIGNATURE_PREFIX",
    "IntegrityResult",
    "canonical_plugin_spec",
    "compute_fingerprint",
    "compute_signature",
    "compute_toolset_fingerprint",
    "verify_plugin_config",
    "verify_signature",
]
