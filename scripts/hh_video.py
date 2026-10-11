#!/usr/bin/env python3
"""HuahuaApi 视频生成命令行工具（文生视频 / 图生视频）。

一条命令走完「填 env → 自检 → 提交 → 轮询 → 下载」全流程：

    hh_video.py init-env              # 第一次使用：生成 env 模板 + 开户/充值引导
    hh_video.py check                 # 自检：key 是否有效、能用到哪些模型
    hh_video.py models                # 列出可用模型
    hh_video.py submit --prompt "..." # 提交任务（真计费），返回 task id
    hh_video.py status <TASK_ID>
    hh_video.py wait <TASK_ID>        # 轮询到终态
    hh_video.py fetch <TASK_ID> -o out.mp4
    hh_video.py run --prompt "..." -o out.mp4   # 一条龙：失败自动换模型重试
    hh_video.py upload a.png          # （可选）本地图传公共图床拿公网 URL

env 查找顺序：--env 指定 → ./env → ./huahuaapi.env → ./.env → ~/.config/huahuaapi/env
找不到 env 时不报错了事，而是打印完整的开户/充值/配置引导并自动生成模板文件。

env 变量名兼容两套写法（本 skill 的 HUAHUA_* 与 old naming R2V_*），
所以 fight-scene-r2v-delivery 那套 env 可以直接拿来用。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("错误：需要 requests（脚本唯一的第三方依赖）。安装：\n"
          "  python3 -m pip install requests\n"
          "或装进虚拟环境后再用该环境的 python3 运行本脚本。",
          file=sys.stderr)
    raise SystemExit(3)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = ROOT / "assets" / "templates" / "env.sample"

SITE = "https://huahuatv.lol"
DEFAULT_BASE = "https://api.huahuaapi.com/v1"
SEARCH_PATHS = ["env", "huahuaapi.env", ".env", os.path.expanduser("~/.config/huahuaapi/env")]


class Recoverable(Exception):
    """可恢复失败：渠道瞬时故障，换模型或稍后重试有机会成功。"""


class Fatal(Exception):
    """不可恢复：参数/鉴权/路径错误，重试没用。"""


# ── 配置 ────────────────────────────────────────────────────────────

def load_env_file(path: Path) -> dict:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k] = v
    return env


def pick(env: dict, *names, default: str = "") -> str:
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return default


def mask(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "***"


def guide(missing: str | None = None) -> Path:
    """首次使用引导：打印开户步骤并生成 env 模板，返回模板路径。"""
    print("=" * 66)
    print("还没找到可用的 env 配置，先花 1 分钟完成这三步：")
    print("=" * 66)
    print()
    print(f"  1) 开通账号：打开 {SITE} 注册并登录")
    print("     （找不到入口就在站内找「API / 开放接口」或问你的渠道商拿地址）")
    print("  2) 创建密钥并充值：在控制台生成 API Key（形如 sk-xxxx），")
    print("     并先充值——余额不足会在提交时直接被拒，不会排队")
    print("  3) 把 key 填进 env：模板已生成，改这一行就行")
    print()
    dst = Path.cwd() / "huahuaapi.env"
    if not dst.exists():
        src = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.is_file() else \
            "HUAHUA_API_BASE=https://api.huahuaapi.com/v1\nHUAHUA_API_KEY=sk-在这里填你的密钥\n"
        dst.write_text(src, encoding="utf-8")
        print(f"     已生成模板：{dst}")
    print("     HUAHUA_API_KEY=sk-xxxx")
    print()
    print("填好后先自检（不花钱、不出片）：")
    print(f"  python hh_video.py check --env {dst}")
    print()
    print("更多说明见 references/api.md")
    if missing:
        print(f"（当前查找路径：{missing}）")
    return dst


def resolve_env(explicit: str | None) -> tuple[dict, Path]:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise Fatal(f"指定的 env 文件不存在：{p}")
        return load_env_file(p), p
    for c in SEARCH_PATHS:
        p = Path(c).expanduser()
        if p.is_file():
            return load_env_file(p), p
    raise Fatal("未找到 env")


def build_cfg(env: dict) -> dict:
    base = pick(env, "HUAHUA_API_BASE", "R2V_API_BASE", default=DEFAULT_BASE).rstrip("/")
    key = pick(env, "HUAHUA_API_KEY", "R2V_API_KEY", "ARK_API_KEY")
    if not key:
        raise Fatal("env 里没有 HUAHUA_API_KEY。先按上面的步骤去开通并拿到密钥。")
    cfg = {
        "base": base,
        "key": key,
        "endpoint": pick(env, "HUAHUA_ENDPOINT", "R2V_ENDPOINT", default="/videos"),
        "asset_key": pick(env, "HUAHUA_ASSET_KEY", "R2V_ASSET_KEY", default="reference_images"),
        "auth_scheme": pick(env, "HUAHUA_AUTH_SCHEME", "R2V_AUTH_SCHEME", default="Bearer"),
        "timeout": int(pick(env, "HUAHUA_HTTP_TIMEOUT", "R2V_HTTP_TIMEOUT", default="600")),
        "download_auth": pick(env, "HUAHUA_DOWNLOAD_AUTH", "R2V_DOWNLOAD_AUTH",
                              default="1") not in ("0", "false", "False"),
        "task_id_keys": [k.strip() for k in
                         pick(env, "HUAHUA_TASK_ID_KEYS", "R2V_TASK_ID_KEYS",
                              default="id,data.id").split(",") if k.strip()],
        "status_tpl": pick(env, "HUAHUA_STATUS_URL_TEMPLATE", "R2V_STATUS_URL_TEMPLATE",
                           default="{base}/videos/{task_id}"),
        "content_tpl": pick(env, "HUAHUA_CONTENT_URL_TEMPLATE", "R2V_CONTENT_URL_TEMPLATE",
                            default="{base}/videos/{task_id}/content"),
        "models": [pick(env, f"HUAHUA_MODEL{sfx}", f"R2V_MODEL{sfx}")
                   for sfx in ("", "2", "3", "4", "5", "6")],
        "extra_params": {},
        "extra_headers": {},
    }
    cfg["models"] = [m for m in cfg["models"] if m]
    raw = pick(env, "HUAHUA_MODEL_PARAMS", "R2V_MODEL_PARAMS")
    if raw:
        try:
            cfg["extra_params"] = json.loads(raw)
        except ValueError as e:
            raise Fatal(f"HUAHUA_MODEL_PARAMS 不是合法 JSON：{e}")
    raw_h = pick(env, "HUAHUA_EXTRA_HEADERS", "R2V_EXTRA_HEADERS")
    if raw_h:
        try:
            cfg["extra_headers"] = json.loads(raw_h)
        except ValueError as e:
            raise Fatal(f"HUAHUA_EXTRA_HEADERS 不是合法 JSON：{e}")
    return cfg


def auth_headers(cfg: dict) -> dict:
    h = {"Authorization": f"{cfg['auth_scheme']} {cfg['key']}"}
    h.update(cfg["extra_headers"])
    return h


# ── 接口调用 ─────────────────────────────────────────────────────────

def dig(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            if not cur:
                return None
            cur = cur[0]
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def endpoint_url(cfg: dict) -> str:
    ep = cfg["endpoint"]
    return ep if ep.startswith("http") else f"{cfg['base']}/{ep.lstrip('/')}"


SUBMIT_SUCCESS_ENDED = ("completed", "succeeded", "success", "done")


def submit_video(cfg: dict, payload: dict):
    url = endpoint_url(cfg)
    try:
        r = requests.post(url, headers={**auth_headers(cfg), "Content-Type": "application/json"},
                          json=payload, timeout=cfg["timeout"])
    except requests.RequestException as e:
        raise Recoverable(f"网络异常 {type(e).__name__}: {e}")
    if r.status_code >= 500:
        raise Recoverable(f"HTTP {r.status_code} {r.text[:200]}")
    if r.status_code >= 400:
        raise Fatal(f"HTTP {r.status_code} {r.text[:300]}")
    try:
        body = r.json()
    except ValueError:
        raise Fatal(f"提交响应不是 JSON：{r.text[:200]}")
    tid = None
    for k in cfg["task_id_keys"]:
        tid = dig(body, k)
        if tid:
            break
    if not tid:
        raise Fatal(f"响应里取不到任务 ID（已试 {cfg['task_id_keys']}）：{json.dumps(body, ensure_ascii=False)[:300]}")
    return body, str(tid)


def get_task(cfg: dict, task_id: str) -> dict:
    url = cfg["status_tpl"].format(base=cfg["base"], endpoint=cfg["endpoint"].lstrip("/"),
                                   task_id=task_id)
    try:
        r = requests.get(url, headers=auth_headers(cfg), timeout=cfg["timeout"])
    except requests.RequestException as e:
        raise Recoverable(f"轮询网络异常 {type(e).__name__}: {e}")
    if r.status_code == 409:          # 任务未完成，继续等
        return {"status": "processing", "progress": 0}
    if r.status_code == 404:
        raise Fatal(f"查不到任务 {task_id}（可能已超过 48 小时保留期被回收）")
    if r.status_code >= 500:
        raise Recoverable(f"轮询 HTTP {r.status_code} {r.text[:200]}")
    if r.status_code >= 400:
        # 该站用 400 + task_not_exist 表示任务查不到（不是 404），单独给一句人话
        if "task_not_exist" in r.text[:300]:
            raise Fatal(f"查不到任务 {task_id}：可能已超过 48 小时保留期被回收，"
                        f"或 id 记错。成片务必在出片后尽快落盘，回收后只能重新生成。")
        raise Fatal(f"轮询 HTTP {r.status_code} {r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        raise Fatal(f"轮询响应不是 JSON：{r.text[:200]}")


def wait_task(cfg: dict, task_id: str, timeout: int, interval: float, quiet: bool = False):
    t0 = time.time()
    last = ""
    while True:
        resp = get_task(cfg, task_id)
        status = str(dig(resp, "status") or dig(resp, "data.status") or "").lower()
        prog = dig(resp, "progress") or dig(resp, "data.progress") or ""
        tag = f"{status} {prog}%".strip()
        if tag != last and not quiet:
            print(f"  [{int(time.time() - t0):>4}s] {tag}", flush=True)
            last = tag
        if status in ("completed", "succeeded", "success", "done"):
            return resp, status
        if status in ("failed", "error", "cancelled", "canceled", "expired"):
            raise Recoverable(f"任务终态 {status}：{json.dumps(resp, ensure_ascii=False)[:300]}")
        if time.time() - t0 > timeout:
            raise Recoverable(f"轮询超时 {timeout}s（未完成）。任务会继续在服务端跑，"
                              f"随时用 hh_video.py wait {task_id} 接着等，不要重复提交。")
        time.sleep(interval)


def download_video(cfg: dict, task_id: str, out: Path):
    url = cfg["content_tpl"].format(base=cfg["base"], endpoint=cfg["endpoint"].lstrip("/"),
                                    task_id=task_id)
    headers = auth_headers(cfg) if cfg["download_auth"] else {}
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, headers=headers, stream=True, timeout=cfg["timeout"]) as r:
            if r.status_code >= 400:
                raise Fatal(f"下载失败 HTTP {r.status_code} {r.text[:200]}"
                            f"（403 试试把 HUAHUA_DOWNLOAD_AUTH 设为 1）")
            with open(out, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        fh.write(chunk)
    except requests.RequestException as e:
        raise Recoverable(f"下载网络异常 {type(e).__name__}: {e}")
    mb = out.stat().st_size / (1 << 20)
    if mb < 0.05:
        raise Fatal(f"下载文件过小（{mb:.2f} MB），可能不是有效视频：{out}")
    return out, mb


# ── 子命令 ──────────────────────────────────────────────────────────

def cmd_init_env(args) -> int:
    dst = Path(args.out).expanduser() if args.out else Path.cwd() / "huahuaapi.env"
    if dst.exists() and not args.force:
        print(f"已存在 {dst}（--force 覆盖）。直接改里面的 HUAHUA_API_KEY。")
    else:
        text = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.is_file() else \
            "HUAHUA_API_BASE=https://api.huahuaapi.com/v1\nHUAHUA_API_KEY=sk-在这里填你的密钥\n"
        dst.write_text(text, encoding="utf-8")
        print(f"已生成 env 模板：{dst}")
    print()
    print(f"接下来两步：① 去 {SITE} 注册并充值；② 把控制台生成的 API Key 填进 HUAHUA_API_KEY")
    print(f"然后自检：python hh_video.py check --env {dst}")
    return 0


def cmd_check(args) -> int:
    env, path = resolve_env(args.env)
    cfg = build_cfg(env)
    print(f"env：{path}  key：{mask(cfg['key'])}  base：{cfg['base']}")
    ok_models = False
    try:
        r = requests.get(f"{cfg['base']}/models", headers=auth_headers(cfg), timeout=20)
        if r.status_code == 200:
            data = r.json().get("data") or []
            ok_models = True
            print(f"✓ 密钥有效，可用模型 {len(data)} 个")
            if args.list_models:
                for m in data:
                    print("   ", m.get("id") or m.get("model"))
        else:
            print(f"✗ 拉模型列表失败 HTTP {r.status_code}：{r.text[:200]}")
    except requests.RequestException as e:
        print(f"✗ 连不上 {cfg['base']}：{type(e).__name__} {e}")
    try:
        b = requests.get(f"{cfg['base']}/dashboard/billing/subscription",
                         headers=auth_headers(cfg), timeout=20)
        if b.status_code == 200:
            print(f"✓ 账户可查询（has_payment_method="
                  f"{b.json().get('has_payment_method')}）")
        elif b.status_code == 401:
            print("✗ 密钥无效或无权限（401），请回控制台确认 key 是否启用")
    except requests.RequestException:
        pass
    print("\n下一步：hh_video.py models 看型号，或 hh_video.py submit --dry-run 先核对请求体")
    return 0 if ok_models else 1


def cmd_models(args) -> int:
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    r = requests.get(f"{cfg['base']}/models", headers=auth_headers(cfg), timeout=30)
    if r.status_code != 200:
        raise Fatal(f"HTTP {r.status_code} {r.text[:200]}")
    data = r.json().get("data") or []
    kw = (args.grep or "").lower()
    n = 0
    for m in data:
        mid = str(m.get("id") or m.get("model") or "")
        if kw and kw not in mid.lower():
            continue
        n += 1
        print(mid)
    if args.grep:
        print(f"（匹配 {n} / 共 {len(data)} 个）")
    else:
        print(f"（共 {len(data)} 个）")
    return 0


def read_prompt(args) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
    if args.prompt:
        return args.prompt.strip()
    raise Fatal("需要 --prompt 或 --prompt-file")


def base_payload(cfg: dict, args, model: str) -> dict:
    payload = {"model": model, "prompt": read_prompt(args)}
    images = list(args.image or [])
    if images:
        payload[cfg["asset_key"]] = images
    params = dict(cfg["extra_params"])
    for kv in args.param or []:
        if "=" not in kv:
            raise Fatal(f"--param 需要 k=v 形式，收到 {kv}")
        k, v = kv.split("=", 1)
        try:
            v = json.loads(v)       # 数字/布尔自动转类型
        except ValueError:
            pass
        params[k] = v
    if args.duration is not None:
        params["duration"] = args.duration
    if args.size:
        params["size"] = args.size
    payload.update(params)
    return payload


def cmd_submit(args) -> int:
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    model = args.model or (cfg["models"][0] if cfg["models"] else "")
    if not model:
        raise Fatal("没有模型：用 --model 指定，或在 env 里配 HUAHUA_MODEL")
    payload = base_payload(cfg, args, model)
    if args.dry_run:
        print(f"[dry-run] POST {endpoint_url(cfg)}  （不会真的提交，不计费）")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    body, tid = submit_video(cfg, payload)
    already = str(dig(body, "status") or "").lower() in SUBMIT_SUCCESS_ENDED
    if args.json:
        print(json.dumps({"task_id": tid, "model": model, "raw": body}, ensure_ascii=False))
    else:
        print(f"已提交  task_id={tid}  model={model}"
              + ("（同步返回已完成，可直接 fetch）" if already else ""))
        print(f"轮询/取回：hh_video.py wait {tid}   /   hh_video.py fetch {tid} -o out.mp4")
    return 0


def cmd_status(args) -> int:
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    resp = get_task(cfg, args.task_id)
    print(json.dumps(resp, ensure_ascii=False, indent=2) if not args.json
          else json.dumps(resp, ensure_ascii=False))
    return 0


def cmd_wait(args) -> int:
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    resp, status = wait_task(cfg, args.task_id, args.timeout, args.poll_interval)
    if args.json:
        print(json.dumps({"task_id": args.task_id, "status": status, "raw": resp},
                         ensure_ascii=False))
    else:
        print(f"任务 {args.task_id} → {status}")
    return 0


def cmd_fetch(args) -> int:
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    out, mb = download_video(cfg, args.task_id, Path(args.out).expanduser())
    print(f"已下载 {out}（{mb:.1f} MB，接口原始文件，未转码）")
    return 0


def cmd_run(args) -> int:
    """一条龙：按顺序试候选模型，直到成功取回一条视频。"""
    env, _ = resolve_env(args.env)
    cfg = build_cfg(env)
    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models \
        else (cfg["models"] or [])
    if not models:
        raise Fatal("没有候选模型：用 --models a,b 指定，或在 env 里配 HUAHUA_MODEL")
    payload0 = base_payload(cfg, args, models[0])
    if args.dry_run:
        print(f"[dry-run] POST {endpoint_url(cfg)}  候选模型 {models}")
        print(json.dumps(payload0, ensure_ascii=False, indent=2))
        return 0
    out = Path(args.out).expanduser()
    print(f"候选模型队列：{models}   输出：{out}")
    for i, model in enumerate(models, 1):
        payload = dict(payload0)
        payload["model"] = model
        print(f"\n=== [{i}/{len(models)}] {model} ===")
        try:
            body, tid = submit_video(cfg, payload)
            print(f"  已受理 task_id={tid}  model={model}")
        except Recoverable as e:
            print(f"  ✗ 提交失败（可恢复）：{str(e)[:200]}")
            time.sleep(args.backoff)
            continue
        except Fatal as e:
            print(f"  ✗ 提交失败：{str(e)[:300]}")
            continue
        try:
            _, status = wait_task(cfg, tid, args.timeout, args.poll_interval)
            if status not in SUBMIT_SUCCESS_ENDED:
                raise Recoverable(f"终态 {status}")
            _, mb = download_video(cfg, tid, out)
        except (Recoverable, Fatal) as e:
            kind = "任务失败" if isinstance(e, Recoverable) else "错误"
            print(f"  ✗ {kind}：{str(e)[:300]}")
            if isinstance(e, Recoverable):
                print(f"    提示：任务可能仍在服务端跑，可用 hh_video.py wait {tid} 续等")
            if args.backoff:
                time.sleep(args.backoff)
            continue
        print(f"✓ 成功：{out}（{mb:.1f} MB）  model={model}  task_id={tid}")
        if args.save:
            Path(args.save).expanduser().write_text(
                json.dumps({"task_id": tid, "model": model, "output": str(out)},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  任务信息已写入 {args.save}")
        return 0
    print("\n所有候选模型都失败了。常见原因：该分组下模型没有可用渠道、余额不足、"
          "参数不被支持。看 references/api.md 的排错表。", file=sys.stderr)
    return 1


def cmd_upload(args) -> int:
    """把本地图传到公共图床，拿公网 URL 给 reference_images 用。"""
    env, _ = resolve_env(args.env)
    base = pick(env, "HUAHUA_UPLOAD_ENDPOINT", "R2V_UPLOAD_ENDPOINT", default="https://uguu.se/upload")
    field = pick(env, "HUAHUA_UPLOAD_FIELD", "R2V_UPLOAD_FIELD", default="files[]")
    keys = [k.strip() for k in pick(env, "HUAHUA_UPLOAD_URL_KEYS", "R2V_UPLOAD_URL_KEYS",
                                    default="files[0].url,url").split(",") if k.strip()]
    urls = []
    for f in args.files:
        p = Path(f).expanduser()
        if not p.is_file():
            print(f"跳过（找不到文件）：{p}")
            continue
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        with open(p, "rb") as fh:
            r = requests.post(base, files={field: (p.name, fh, mime)}, timeout=120)
        if r.status_code >= 400:
            print(f"✗ {p.name} → HTTP {r.status_code} {r.text[:160]}")
            continue
        try:
            body = r.json()
        except ValueError:
            print(f"✗ {p.name} → 响应不是 JSON：{r.text[:160]}")
            continue
        u = None
        for k in keys:
            u = dig(body, k)
            if u:
                break
        if not u:
            print(f"✗ {p.name} → 响应里找不到 URL（已试 {keys}）：{json.dumps(body)[:200]}")
            continue
        print(f"✓ {p.name} → {u}")
        urls.append(str(u))
    if urls:
        print("\n拿去当参考图：--image " + " --image ".join(urls))
    return 0 if urls else 1


# ── CLI ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # 注意：--env 挂在父解析器上再分发给每个子命令，这样
    #   hh_video.py models --env ./env      和  hh_video.py --env ./env models
    # 两种写法都能用（argparse 里主解析器与子解析器同名同 dest 会互相覆盖，故只留一处）。
    env_common = argparse.ArgumentParser(add_help=False)
    env_common.add_argument("--env", default=None,
                            help="env 文件路径（默认依次找 env / huahuaapi.env / .env / "
                                 "~/.config/huahuaapi/env）")

    ap = argparse.ArgumentParser(
        prog="hh_video.py", description="HuahuaApi 视频生成：提交 / 轮询 / 下载",
        parents=[env_common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-env", parents=[env_common], help="生成 env 模板并给出开户引导")
    p.add_argument("--out", help="输出路径，默认 ./huahuaapi.env")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_env)

    p = sub.add_parser("check", parents=[env_common], help="自检：密钥是否有效、可用模型数")
    p.add_argument("--list-models", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("models", parents=[env_common], help="列出可用模型")
    p.add_argument("--grep", help="按关键字过滤，如 seedance")
    p.set_defaults(func=cmd_models)

    def add_common(p, with_out=False):
        p.add_argument("--prompt")
        p.add_argument("--prompt-file", help="从文件读 Prompt（长文本推荐）")
        p.add_argument("--image", action="append", help="参考图公网 URL，可重复给多个")
        p.add_argument("--model", help="模型名，覆盖 env 的 HUAHUA_MODEL")
        p.add_argument("--duration", type=int, help="时长秒数")
        p.add_argument("--size", help="分辨率，如 1280x720")
        p.add_argument("--param", action="append", help="附加请求参数 k=v，可重复")
        if with_out:
            p.add_argument("-o", "--out", default="out.mp4")
        p.add_argument("--dry-run", action="store_true", help="只打印请求体，不提交不计费")

    p = sub.add_parser("submit", parents=[env_common], help="提交任务（会计费）")
    add_common(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", parents=[env_common], help="查询任务状态")
    p.add_argument("task_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("wait", parents=[env_common], help="轮询到终态")
    p.add_argument("task_id")
    p.add_argument("--timeout", type=int, default=2400)
    p.add_argument("--poll-interval", type=float, default=12.0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("fetch", parents=[env_common], help="下载成片")
    p.add_argument("task_id")
    p.add_argument("-o", "--out", default="out.mp4")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("run", parents=[env_common], help="一条龙：失败自动换模型重试")
    add_common(p, with_out=True)
    p.add_argument("--models", help="候选模型队列，逗号分隔")
    p.add_argument("--timeout", type=int, default=2400, help="单个任务的轮询超时")
    p.add_argument("--poll-interval", type=float, default=12.0)
    p.add_argument("--backoff", type=float, default=30.0, help="失败后等待秒数")
    p.add_argument("--save", help="把 task_id / model 写入该文件")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("upload", parents=[env_common], help="本地图传公共图床拿公网 URL")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_upload)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Fatal as e:
        if str(e) == "未找到 env":
            guide(" / ".join(SEARCH_PATHS[:3]) + f" / {SEARCH_PATHS[3]}")
            return 2
        print(f"错误：{e}", file=sys.stderr)
        return 2
    except Recoverable as e:
        print(f"错误（可重试）：{e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\n已中断。任务仍在服务端执行，用 hh_video.py wait <task_id> 续等。",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
