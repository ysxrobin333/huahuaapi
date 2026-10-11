# huahuaapi-video-gen

用 [HuahuaApi](https://huahuatv.lol) 的视频生成接口出片——**文生视频 / 图生视频**，
一个脚本覆盖「配置 env → 连通性自检 → 提交任务 → 轮询 → 下载成片」全流程。

只做通用视频生成，**不绑定任何业务口径**：不做内容判定、不做交付验收、不写 metadata。
想拿它干别的活（批量交付、打斗占比质检等），在你自己的流程里调这个脚本就行。

```
python3 scripts/hh_video.py run --prompt-file examples/prompt.txt --duration 10 -o out.mp4
```

---

## 目录

- [第一次使用](#第一次使用)
- [命令一览](#命令一览)
- [配置 env](#配置-env)
- [图生视频：参考图要公网 URL](#图生视频参考图要公网-url)
- [实测经验五条](#实测经验五条)
- [常见报错对照](#常见报错对照)
- [安装为 WorkBuddy Skill](#安装为-workbuddy-skill)
- [安全与计费](#安全与计费)
- [License](#license)

---

## 第一次使用

脚本在**找不到 env 时不会干报错**，而是把下面三步打印出来，并自动在当前目录生成
`huahuaapi.env` 模板，你只填一行就能跑。

1. **开通账号** —— 打开 <https://huahuatv.lol> 注册并登录
2. **创建密钥并充值** —— 控制台生成 API Key（形如 `sk-xxxx`）。
   **一定要先充值**，余额不足会在提交时直接被拒绝
3. **填进 env 并自检**：

```bash
git clone https://github.com/ysxrobin333/huahuaapi.git
cd huahuaapi
python3 -m pip install requests          # 唯一第三方依赖

python3 scripts/hh_video.py init-env     # 生成 ./huahuaapi.env
# 编辑 huahuaapi.env，改这一行：HUAHUA_API_KEY=sk-xxxx

python3 scripts/hh_video.py check --env ./huahuaapi.env
# 自检不花钱、不出片：验证 key 是否有效 + 列出能用到几个模型
```

看到 `✓ 连通正常` 和模型数量就说明配置好了。

## 命令一览

| 命令 | 作用 | 是否计费 |
|---|---|---|
| `init-env` | 生成 env 模板 + 开户引导 | 否 |
| `check` | 自检：key 有效性、可用模型数 | 否 |
| `models [--grep 关键字]` | 列出账号可用模型 | 否 |
| `submit [--dry-run]` | 提交任务，返回 `task_id` | **是**（`--dry-run` 除外） |
| `status <task_id>` | 查单次状态 | 否 |
| `wait <task_id>` | 轮询到终态 | 否 |
| `fetch <task_id> -o out.mp4` | 下载成片 | 否（重下载不重复计费） |
| `run` | 一条龙：提交 → 轮询 → 下载，失败自动换模型 | **是** |
| `upload <本地图>` | 本地图传公共图床，换公网 URL | 否 |

```bash
# 先核对请求体，不花钱不出片（强烈建议长片之前都先跑一次）
python3 scripts/hh_video.py submit --prompt-file examples/prompt.txt \
    --duration 10 --size 1280x720 --dry-run

# 文生视频（失败自动按模型队列重试）
python3 scripts/hh_video.py run --prompt-file examples/prompt.txt \
    --duration 10 --size 1280x720 -o out.mp4

# 图生视频
python3 scripts/hh_video.py upload role.png
python3 scripts/hh_video.py run --prompt "两人对峙" --image https://.../role.png -o out.mp4

# 分步执行（想自己控节奏时）
python3 scripts/hh_video.py submit --prompt "..." --json    # → task_id
python3 scripts/hh_video.py wait task_xxx --timeout 2400
python3 scripts/hh_video.py fetch task_xxx -o out.mp4
```

每个子命令都有 `--help`。`--env` 放在子命令前后都行：
`hh_video.py check --env x` 和 `hh_video.py --env x check` 等价。

## 配置 env

只有 `HUAHUA_API_KEY` **必填**，其余都有合理默认值。复制模板改一行即可：

```bash
cp assets/templates/env.sample huahuaapi.env
```

| 键 | 默认 | 说明 |
|---|---|---|
| `HUAHUA_API_BASE` | `https://api.huahuaapi.com/v1` | 接口根地址 |
| `HUAHUA_API_KEY` | — | **必填**，控制台生成的密钥 |
| `HUAHUA_MODEL` / `MODEL2~6` | — | 默认模型 + 失败轮换队列 |
| `HUAHUA_MODEL_PARAMS` | 空 | JSON，如 `{"duration":10,"size":"1280x720"}` |
| `HUAHUA_ASSET_KEY` | `reference_images` | 图生视频的参考图字段名 |
| `HUAHUA_HTTP_TIMEOUT` | 600 | 提交可能同步阻塞很久，别调小 |
| `HUAHUA_DOWNLOAD_AUTH` | 1 | `/content` 下载要带鉴权头 |
| `HUAHUA_UPLOAD_*` | uguu.se | `upload` 子命令用的公共图床 |

> **兼容性**：所有 `HUAHUA_*` 都可用同名 `R2V_*` 代替（`R2V_API_KEY`、`R2V_MODEL`、
> `R2V_MODEL_PARAMS`…）。如果你已经有另一套脚本在用 `R2V_*` 命名的 env，
> 直接 `--env` 指过来复用即可，不用重建。

## 图生视频：参考图要公网 URL

接口**不接受本地路径，也不收 base64**（会报
`reference images must contain public http/https URLs`）。两个办法：

1. **已有公网图片链接** → 直接 `--image https://...`，可重复传多张
2. **只有本地文件** → 先 `upload`：

```bash
python3 scripts/hh_video.py upload role.png     # → https://.../role.png
```

默认用公共图床 `uguu.se` 兜底（本机实测可达、公网可直取）。注意**链接有时效（约 24h）**，
出片后及时归档原图；换图床改 `HUAHUA_UPLOAD_ENDPOINT` / `FIELD` / `URL_KEYS` 三个键。

## 实测经验五条

不知道这几条基本会踩一遍：

1. **只有 `POST /v1/videos`（复数）**，`/v1/video/generations` 会 503。
2. **成片不在 JSON 里**，要 `GET /v1/videos/{id}/content` 拉二进制，且**必须带鉴权头**。
3. **排队 10~25 分钟是常态**。超时了**别重新提交**（会重复扣费），
   用同一个 `task_id` 再 `wait` 一次续等即可。
4. **成片只保留 48 小时**，出来就落盘。错过只能重新生成并再次计费。
5. **先短后长**：先 `--duration 5` 验证模型可用性与构图，再上长片，能省下大量试错成本。

另外聚合站的「该分组下这个型号没有可用渠道」「上游暂时不可用」是**常态**，
`run` 会按顺序试 env 里的模型队列并跳过失败项，失败通常不计费。
想看它到底试了什么，看终端末尾打印的 failed attempts。

关于分辨率（2026-10 实测）：Seedance / SD 系列当时**实际只出 720p**，
`size=1920x1080` 会被静默忽略或直接报 `resolution 1080p is not supported`；
型号 `sd-2.5-1080p-max` 名义支持 1080p 但渠道不稳定。以 `python3 scripts/hh_video.py models`
实时列出的为准。

## 常见报错对照

| 现象 | 含义 | 处理 |
|---|---|---|
| `402` / 余额不足 | 没充值 | 去控制台充值 |
| `401` | key 无效或过期 | 重新生成 key |
| `503 fail_to_fetch_task` | 上游暂时不可用 | 等一会儿重试 / 换个模型，一般不计费 |
| `502` / `500` | 上游异常 | 同上 |
| `409` | 任务还没完成 | 继续 `wait` 同一个 ID |
| `400 task_not_exist` | 任务被回收 | 已超 48 小时保留期，只能重新生成 |
| `resolution 1080p is not supported` | 该型号不支持 | 换型号或降到 720p |
| `no available channel` | 该分组下无可用渠道 | 换模型 |

完整端点清单、请求体字段、模型命名规律见 [`references/api.md`](references/api.md)。

## 安装为 WorkBuddy Skill

整个仓库本身就是一份标准 skill（`SKILL.md` 带 frontmatter）。装到用户级技能目录：

```bash
cp -R . ~/.workbuddy/skills/huahuaapi-video-gen
```

之后在对话里说「用 HuahuaApi 生成一段视频」即可触发；脚本会自动引导配置 env。

## 安全与计费

- **env 内含密钥**：`.gitignore` 已排除所有 env 文件，只提交模板 `env.sample`。
  不要把真实 env 提交到仓库，也不要放进要交给别人的目录。
- 脚本输出的 key 一律**脱敏**（只显示前 6 后 4 位）。
- **`submit` / `run` 会真实扣费**；核对参数一律先跑 `--dry-run`。
- 失败重试会换模型重试，**但只要提交成功就开始计费**——重复提交同一内容会重复扣钱。

## License

[MIT](LICENSE) © 2026 ysxrobin333

本项目是第三方工具的**非官方**封装，与 HuahuaApi 官方无隶属关系；
接口行为、模型可用性、计费规则以官方为准。
