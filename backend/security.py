"""
微信内容安全检测（云托管「开放接口服务」免鉴权调用）

=== 为什么这样调（三个反直觉的点，踩错任一都会失败） ===

1. **必须用 HTTP，不能用 HTTPS。**
   云托管在容器旁挂了一个组件，由它把 access_token 注入并转发。走 HTTPS 时
   请求会经过该组件的自签证书处理，直接报 SSLHandshakeException。
   官方给的示例地址就是 `http://api.weixin.qq.com/wxa/msg_sec_check`。

2. **不能自己拼 access_token，也不需要 AppSecret。**
   控制台「云调用 → 云调用权限配置」里登记 `/wxa/msg_sec_check` 后，
   环境下所有服务都能免鉴权调用。自己维护 token 生命周期是旧做法（已下线）。

3. **必须带 openid。** msg_sec_check 的 v2 接口要求 openid 标识检测来源，
   云托管会把调用者身份放在 `X-WX-OPENID` header 里，直接透传即可。

=== 失败策略：fail-open，不是 fail-closed ===

微信接口抖动、控制台漏配白名单、本地开发没有旁挂组件——这些情况下
如果一律拦截，整个问答服务就不可用了，代价远大于收益。所以：

  · 只有 errcode=87014（明确判定为违规）才拦截
  · 超时 / 连接失败 / 其他 errcode → 放行 + 记日志

本地开发自动禁用：检测容器内是否存在云托管注入的 token 文件，
不存在就不发请求（否则每条提问都白等一次外网超时）。
"""

import json
import logging
import os
import time
import urllib.request

logger = logging.getLogger("security")

MSG_SEC_URL = "http://api.weixin.qq.com/wxa/msg_sec_check"

# 云托管把 access_token 周期性推送到容器里这个路径；文件存在 = 处于云托管环境
TOKEN_FILE = "/.tencentcloudbase/wx/cloudbase_access_token"

RISKY_ERRCODE = 87014          # 内容含违法违规信息
SCENE_COMMENT = 2              # 1=资料 2=评论 3=论坛 4=社交日志；问答按「评论」报

# 单次检测的超时上限。微信侧通常 50~200ms，1.2s 足够；
# 超了就走 fail-open，不能让它吃掉 12s 的生成预算。
DEFAULT_TIMEOUT = float(os.getenv("SEC_CHECK_TIMEOUT", "1.2"))

# 熔断：连续失败达到阈值后，一段时间内直接放行、不再发请求。
# 动机与向量通道熔断一致——外部依赖挂掉时，最坏的结果不是"检测没生效"，
# 而是每条提问都白等 0.3~1.2s，把 12s 预算吃光、把 RAG 拖成降级。
CIRCUIT_THRESHOLD = int(os.getenv("SEC_CHECK_FAIL_THRESHOLD", "3"))
CIRCUIT_SECONDS = float(os.getenv("SEC_CHECK_CIRCUIT_SECONDS", "300"))

_enabled_flag = os.getenv("SEC_CHECK_ENABLE", "1").strip().lower() not in ("0", "false", "off", "no")
_runtime_available = None      # 惰性探测，避免每次请求都 stat 文件
_fail_streak = 0
_circuit_until = 0.0


def _available() -> bool:
    """判断当前是否具备云调用条件（只在首次判断时探测一次）。"""
    global _runtime_available
    if _runtime_available is None:
        if not _enabled_flag:
            _runtime_available = False
        elif os.getenv("SEC_CHECK_FORCE", "").strip() in ("1", "true", "on"):
            # 允许本地强制开启，用于联调；需自行提供 token 才能调通
            _runtime_available = True
        else:
            _runtime_available = os.path.exists(TOKEN_FILE)
        if not _runtime_available:
            logger.info("[security] 非云托管环境或已禁用，内容检测跳过（局部不发请求）")
    return _runtime_available


def check_text(content: str, openid: str = "", scene: int = SCENE_COMMENT,
               timeout: float = None) -> tuple:
    """检测一段文本。

    返回 (passed, detail)：
      passed=True  可以继续（含 fail-open 放行）
      passed=False 明确违规，调用方应拦截
    detail 是给人看的简短原因，会写进日志，不返回给用户。
    """
    if not content or not content.strip():
        return True, "empty"
    if not _available():
        return True, "disabled"
    if not openid:
        # v2 接口要求 openid 标识检测来源，缺失时调用必然报错（41002 一类）。
        # 小程序走 callContainer 一定有 X-WX-OPENID；走到这里说明是网页版直连调试，
        # 直接跳过比白等一次失败更划算。
        logger.debug("[security] 无 openid，跳过检测")
        return True, "no_openid"

    global _fail_streak, _circuit_until
    if time.time() < _circuit_until:
        return True, "circuit_open"

    payload = {"content": content, "version": 2, "scene": scene}
    if openid:
        payload["openid"] = openid

    try:
        req = urllib.request.Request(
            MSG_SEC_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        # 网络抖动、超时、DNS 失败都不拦人，留痕即可
        _trip_breaker(f"call_failed:{type(e).__name__}")
        return True, f"call_failed:{type(e).__name__}"

    errcode = data.get("errcode", 0)
    if errcode == 0:
        _fail_streak = 0
        return True, "ok"
    if errcode == RISKY_ERRCODE:
        _fail_streak = 0                     # 接口本身是通的，只是内容有问题
        logger.warning("[security] 判定违规 scene=%s len=%d openid=%s",
                       scene, len(content), (openid[:6] + "***") if openid else "-")
        return False, "risky"
    # 40164/41001/41004（token 无效或缺失）、48001（无权限）等属于配置问题，
    # 不该让用户背锅；但也不能每条请求都白等一次，累计几次就熔断。
    _trip_breaker(f"errcode:{errcode}")
    logger.warning("[security] 接口返回非预期 errcode=%s errmsg=%s，放行",
                   errcode, data.get("errmsg", ""))
    return True, f"errcode:{errcode}"


def _trip_breaker(reason: str) -> None:
    """连续失败累计到阈值就熔断一段时间，期间所有检测直接放行。"""
    global _fail_streak, _circuit_until
    _fail_streak += 1
    if _fail_streak >= CIRCUIT_THRESHOLD:
        _circuit_until = time.time() + CIRCUIT_SECONDS
        logger.error("[security] 连续 %d 次失败（最后一次：%s），熔断 %.0fs——"
                     "期间内容检测跳过。请检查控制台「云调用 → 权限配置」是否"
                     "登记了 /wxa/msg_sec_check", _fail_streak, reason, CIRCUIT_SECONDS)
        _fail_streak = 0


def status() -> dict:
    """给 /health 用：让运维一眼看到检测到底有没有在工作、是否已熔断。"""
    remaining = max(0.0, _circuit_until - time.time())
    return {
        "enabled": _enabled_flag,
        "runtime_available": _available(),
        "timeout": DEFAULT_TIMEOUT,
        "circuit_open": remaining > 0,
        "circuit_reset_in": round(remaining, 1),
    }
