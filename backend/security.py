"""
微信内容安全检测（云托管「开放接口服务」免鉴权调用）

=== 先说清楚两代机制的区别，这是最容易踩错的地方 ===

微信云托管历史上提供过两套"免鉴权调用微信接口"的方案，实现原理完全不同：

  【旧版·云调用】已于 2022 年 7 月下线，该时间之后创建的环境一律不支持。
    原理：微信后台周期性把 access_token 推送到容器内的
          `/.tencentcloudbase/wx/cloudbase_access_token` 文件，
          业务代码读文件、把 token 拼进 URL 的 `cloudbase_access_token` 参数。

  【新版·开放接口服务】就是控制台「云调用」页那个开关，本项目用的就是它。
    原理：以**旁加载组件**（sidecar）形式部署到服务中，中转 api.weixin.qq.com
          的请求并自动处理 token 与鉴权。容器内**根本不存在 token 文件**，
          业务代码直接用 HTTP 请求，什么都不用拼。

本项目最初误按旧版思路，用 `os.path.exists(TOKEN_FILE)` 判断"是否具备云调用
条件"——在新版机制下这个文件永远不会生成，判断恒为 False，检测一次请求都没发
出去过，而 /health 上看起来只是"没生效"，极难定位。现已彻底移除该判断。

=== 那么现在怎么判断该不该发请求 ===

用**链路特征**，不用文件、不猜环境：

  `msg_sec_check` v2 要求带 openid 标识检测来源。而 openid 在本项目里来自云托管
  注入的 `X-WX-OPENID` 请求头——**能拿到 openid，就说明这个请求确实是从微信链路
  进来的，也就必然处于云托管环境**，云调用可用。反之（网页直连调试、本地跑
  uvicorn、单元测试）一定没有 openid，直接跳过，既不白等超时也不会误拦。

比环境探测可靠：没有"探测时机早于组件就位"的竞态，也不依赖任何路径/变量名。

=== 三个必须遵守的调用约束 ===

1. **用 HTTP，不要 HTTPS。** 旁加载组件靠拦截明文 HTTP 请求来注入 token，
   走 HTTPS 会因为组件自签证书直接 SSLHandshakeException。
2. **不要自己拼 access_token，也不需要 AppSecret。** 控制台「云调用 → 云调用
   权限配置」登记 `/wxa/msg_sec_check` 后，环境下所有服务免鉴权可用。
3. **必须带 openid**，否则报 41002 一类。

=== 失败策略：fail-open，不是 fail-closed ===

微信接口抖动、控制台漏配权限、旁加载组件没随版本生效——这些情况下一律拦截的话，
整个问答服务就不可用了，代价远大于收益。所以：

  · 只有 errcode=87014（明确判定违规）才拦截
  · 超时 / 连接失败 / 其他 errcode → 放行 + 记日志 + 累计熔断

=== 判断"到底生效了没有"的金标准 ===

官方文档：云调用请求正常时，**响应头会带 `x-openapi-seqid`**。
收到它 = 确实走了云调用链路；没收到 = 只是普通外网请求（多半被拒）。
status() 里的 `via_openapi` 就来自这个头，比任何自检都可信。

=== 另外一条硬要求 ===

官方原文：「如果你在开启『开放接口服务』前，已经存在服务版本，需要在开启
『开放接口服务』后**重新构建版本**才会生效。」
—— 开关是版本级配置，改完必须发新版，重启无效。
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("security")

MSG_SEC_URL = "http://api.weixin.qq.com/wxa/msg_sec_check"

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

_fail_streak = 0
_circuit_until = 0.0

# 观测数据，供 /health 判断"到底通没通"，纯诊断用，不参与业务逻辑
_ok_count = 0                  # 成功调用次数（errcode=0 或 87014 都算通信成功）
_fail_count = 0                # 调用失败次数（网络异常或非预期 errcode）
_last_result = "never"         # 最近一次结果，人看的
_last_seqid = ""               # 最近一次收到的 x-openapi-seqid
_openapi_hits = 0              # 收到过 seqid 的次数，>0 即证明云调用链路真的通了


def _skip_reason(openid: str):
    """返回跳过原因；返回 None 表示应当真正发起调用。

    判据只有两条，都不依赖环境探测：
      1. 显式关闭（SEC_CHECK_ENABLE=0）
      2. 没有 openid —— 说明不是从微信链路进来的，云调用必然不可用
    """
    if not _enabled_flag:
        return "disabled"
    if not openid:
        # 本地 uvicorn、网页直连调试、脚本压测都落在这里。是真的不发请求，
        # 不是"假装发了"——既省掉 1.2s 白等，也避免把外网失败记成熔断。
        return "no_openid"
    return None


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

    skip = _skip_reason(openid)
    if skip:
        return True, skip

    global _fail_streak, _circuit_until, _ok_count, _fail_count
    global _last_result, _last_seqid, _openapi_hits

    if time.time() < _circuit_until:
        return True, "circuit_open"

    payload = {"content": content, "version": 2, "scene": scene, "openid": openid}

    try:
        req = urllib.request.Request(
            MSG_SEC_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            # 官方文档：云调用请求正常时响应头带 x-openapi-seqid。
            # 有它 = 确实走了旁加载组件的链路，这是唯一可信的"生效"证据。
            seqid = resp.headers.get("x-openapi-seqid") or ""
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        _fail_count += 1
        _last_result = f"http_{e.code}"
        _trip_breaker(_last_result)
        return True, _last_result
    except Exception as e:
        # 网络抖动、超时、DNS 失败、非 JSON 都不拦人，留痕即可
        _fail_count += 1
        _last_result = f"call_failed:{type(e).__name__}"
        _trip_breaker(_last_result)
        return True, _last_result

    if seqid:
        _openapi_hits += 1
        _last_seqid = seqid

    errcode = data.get("errcode", 0)

    if errcode == 0:
        _ok_count += 1
        _fail_streak = 0
        _last_result = "ok"
        return True, "ok"

    if errcode == RISKY_ERRCODE:
        # 接口本身是通的，只是内容有问题——不能算失败，更不能触发熔断，
        # 否则连续拦几条违规内容反而把检测能力关掉了。
        _ok_count += 1
        _fail_streak = 0
        _last_result = "risky"
        logger.warning("[security] 判定违规 scene=%s len=%d openid=%s",
                       scene, len(content), (openid[:6] + "***") if openid else "-")
        return False, "risky"

    # 48001（无权限）、41002（缺 openid）、40164/40001（token 相关）等属于配置
    # 或环境问题，不该让用户背锅；但也不能每条请求都白等一次，累计几次就熔断。
    _fail_count += 1
    _last_result = f"errcode:{errcode}"
    _trip_breaker(_last_result)
    logger.warning("[security] 接口返回非预期 errcode=%s errmsg=%s，放行",
                   errcode, data.get("errmsg", ""))
    return True, _last_result


def _trip_breaker(reason: str) -> None:
    """连续失败累计到阈值就熔断一段时间，期间所有检测直接放行。"""
    global _fail_streak, _circuit_until
    _fail_streak += 1
    if _fail_streak >= CIRCUIT_THRESHOLD:
        _circuit_until = time.time() + CIRCUIT_SECONDS
        logger.error("[security] 连续 %d 次失败（最后一次：%s），熔断 %.0fs——"
                     "期间内容检测跳过。请检查：①控制台「云调用 → 云调用权限配置」"
                     "是否登记 /wxa/msg_sec_check；②「开放接口服务」开关是否已打开；"
                     "③打开开关后是否重新构建过服务版本（改开关后必须发新版，重启无效）",
                     _fail_streak, reason, CIRCUIT_SECONDS)
        _fail_streak = 0


def status() -> dict:
    """给 /health 用：让运维一眼看到检测到底有没有在工作。

    runtime_available 是三态，别按布尔理解：
      true     至少成功调用过一次，链路确认可用
      false    调用过但全部失败，链路有问题
      null     还没有带 openid 的请求进来过，尚未验证（刚部署完的常态）
    """
    remaining = max(0.0, _circuit_until - time.time())

    if not _enabled_flag:
        available = False
    elif _ok_count > 0:
        available = True
    elif _fail_count > 0:
        available = False
    else:
        available = None

    s = {
        "enabled": _enabled_flag,
        "runtime_available": available,
        "timeout": DEFAULT_TIMEOUT,
        "ok_count": _ok_count,
        "fail_count": _fail_count,
        "last_result": _last_result,
        "via_openapi": _openapi_hits > 0,
        "circuit_open": remaining > 0,
        "circuit_reset_in": round(remaining, 1),
    }

    if not _enabled_flag:
        s["hint"] = "SEC_CHECK_ENABLE=0，已主动关闭"
    elif available is True:
        s["hint"] = ("云调用链路已验证（收到 x-openapi-seqid）" if _openapi_hits else
                     "检测接口可正常返回，但未收到 x-openapi-seqid——"
                     "说明请求没走旁加载组件，可能只是普通外网直连")
    elif available is False:
        s["hint"] = (f"最近一次失败：{_last_result}。依次检查："
                     f"①「云调用 → 云调用权限配置」是否登记 /wxa/msg_sec_check；"
                     f"②「开放接口服务」开关是否打开；"
                     f"③打开开关后是否重新构建过版本（改开关后必须发新版，重启无效）")
    else:
        s["hint"] = "尚未有带 openid 的请求进来，链路待验证——在小程序里随便问一句即可"
    return s
