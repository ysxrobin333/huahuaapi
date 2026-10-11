---
name: huahuaapi-video-gen
description: 用 HuahuaApi（api.huahuaapi.com）的视频生成接口出片——文生视频、图生视频，覆盖「配置 env → 连通性自检 → 提交任务 → 轮询 → 下载成片」全流程，并在首次使用时引导用户去开通账号、创建 API Key 与充值。当用户要把一段提示词或一张/多张参考图通过 HuahuaApi 生成视频、要查可用模型或任务状态、要重新下载已有任务成片、或抱怨接口报 503/400/409 时使用。与本 skill 相邻的 fight-scene-r2v-delivery 负责「打斗数据交付」，本 skill 只负责通用视频生成，不含任何交付验收逻辑。
license: MIT
metadata:
  version: 1.0.0
  agent_created: true
  author: WorkBuddy
  tags: [huahuaapi, 视频生成, 文生视频, 图生视频, seedance, api-key, 脚本]
---

# HuahuaApi 视频生成

把「调 HuahuaApi 出片」这件事收成一个脚本命令：填 env → 自检 → 提交 → 轮询 → 下载。
通用能力，**不绑定任何业务口径**（不做打斗、不做交付验收、不写 metadata）。
需要做《打斗场景 R2V 数据交付》时改用 `fight-scene-r2v-delivery`。

## 一、第一次使用（必读）

用户第一次用，先走这套引导，不要直接开跑：

1. **开通账号**：打开 <https://huahuatv.lol> 注册登录
2. **创建密钥并充值**：控制台生成 API Key（形如 `sk-xxxx`）——**一定要先充值**，
   余额不足会在提交时被直接拒绝
3. **生成 env 模板**并让用户把 key 填进去：

```bash
PY=python3
SD=path/to/huahuaapi/scripts

$PY $SD/hh_video.py init-env            # 生成 ./huahuaapi.env
# 改一行：HUAHUA_API_KEY=sk-xxxx
$PY $SD/hh_video.py check --env ./huahuaapi.env    # 自检：key 是否有效、能用到几个模型
```

env 找不到时脚本不会干报错，而是把上面三步原样打印出来并自动生成模板文件。

## 二、常用命令

```bash
$SD/hh_video.py models                          # 列出可用模型
$SD/hh_video.py models --grep seedance          # 按关键字筛

# 先核对请求体，不花钱不出片
$SD/hh_video.py submit --prompt-file prompt.txt --duration 10 --size 1280x720 --dry-run

# 文生视频（一条龙：提交→轮询→下载，失败自动换模型）
$SD/hh_video.py run --prompt-file prompt.txt --duration 10 --size 1280x720 -o out.mp4

# 图生视频：参考图需要公网 URL
$SD/hh_video.py upload role.png                 # 拿不到图床时用内置上传
$SD/hh_video.py run --prompt "两人对峙" --image https://.../role.png -o out.mp4

# 分步执行（想自己控节奏时）
$SD/hh_video.py submit --prompt "..." --json    # → task_id
$SD/hh_video.py wait task_xxx --timeout 2400
$SD/hh_video.py fetch task_xxx -o out.mp4
$SD/hh_video.py status task_xxx
```

候选模型写进 env（`HUAHUA_MODEL` / `MODEL2` / `MODEL3`）或临时指定：
`run --models "sd-2.5,seedance2.5-a" --backoff 30`。

## 三、四条硬经验（不知道就会踩）

1. **只有 `POST /v1/videos`（复数）**，`/v1/video/generations` 会 503。
2. **成片不是 JSON 字段，要 `GET /v1/videos/{id}/content` 下载二进制**，且要带鉴权头。
3. **排队 10~25 分钟是常态**，超时了别重提——`wait <同一个 task_id>` 续等即可，
   重复提交会**重复扣费**。
4. **成片只保留 48 小时**，出来就落盘；错过只能重新生成（重生成会再次计费）。

## 四、为什么 run 会换模型

聚合站常见「该分组下这个型号没有可用渠道」「上游暂时不可用」——503/500/502 都属于
可恢复失败，失败通常不计费。`run` 按顺序试队列里的模型，逐个跳过失败项，成功后报告
`task_id` 与文件路径。所有 failed attempts 会在终端直接打出来。

短任务先短后长：先 `--duration 5` 验证模型与构图，再上 `--duration 30` 的长片，
能明显省下试错成本。

## 五、参数与配置

命令行优先于 env。常用项：`--prompt` / `--prompt-file` / `--image`（可重复）/
`--model` / `--duration` / `--size` / `--param k=v`（透传任意附加参数）。

env 键（都有默认，只有 `HUAHUA_API_KEY` 必填）：

| 键 | 默认 | 说明 |
|---|---|---|
| `HUAHUA_API_BASE` | `https://api.huahuaapi.com/v1` | 接口根地址 |
| `HUAHUA_API_KEY` | — | **必填**，控制台生成的密钥 |
| `HUAHUA_MODEL` / `MODEL2~6` | — | 默认模型与轮换队列 |
| `HUAHUA_MODEL_PARAMS` | 空 | JSON，如 `{"size":"1280x720"}` |
| `HUAHUA_HTTP_TIMEOUT` | 600 | 提交可能同步阻塞很久，别调小 |
| `HUAHUA_DOWNLOAD_AUTH` | 1 | 下载带鉴权 |
| `HUAHUA_UPLOAD_*` | uguu.se | `upload` 子命令用的公共图床 |

**兼容性**：所有 `HUAHUA_*` 都可用同名 `R2V_*` 代替（`R2V_API_KEY`、`R2V_MODEL`、
`R2V_MODEL_PARAMS`…），所以 `fight-scene-r2v-delivery` 已经在用的 env 可以直接
`--env` 指过来复用，不必重建。

## 六、资源

- `scripts/hh_video.py` —— 唯一入口，`--help` 与各子命令 `--help` 都有说明
- `assets/templates/env.sample` —— env 模板
- `references/api.md` —— 端点清单、请求体、错误码对照表、模型命名规律

## 七、运行环境

Python 3.9+，唯一第三方依赖是 `requests`：

```bash
python3 -m pip install requests     # 系统环境没有时先装
PY=python3                          # 用哪个 python 都行，只要能 import requests
```

在 WorkBuddy 里作为 skill 使用时，若托管 venv 的地址固定，把 `PY` 换成该 venv 的
绝对路径即可（例如 `~/.workbuddy/binaries/python/envs/default/bin/python`）。

## 八、安全

- env 内含密钥：**不要提交到 git、不要放进要交给别人的目录**（模板里已写明）。
- 脚本输出的 key 一律脱敏（只显示前 6 后 4 位）。
- `--dry-run` 不产生任何费用，核对参数一律先跑它。
