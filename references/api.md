# HuahuaApi 视频接口要点（实测版）

所有结论均来自实跑验证（含失败请求），不是文档照抄。

## 一、端点

| 用途 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 提交生成 | POST | `/v1/videos` | **必须是复数 `/videos`**，单数 `/v1/video/generations` 不存在会 503 |
| 查询任务 | GET | `/v1/videos/{id}` | 返回 `{"id","model","status","progress"}` |
| 下载成片 | GET | `/v1/videos/{id}/content` | 返回 **MP4 二进制**（不是 JSON），**需带鉴权头** |
| 可用模型 | GET | `/v1/models` | 返回 `data[].id`，用来核对当前账号能用到哪些型号 |
| 账户自检 | GET | `/v1/dashboard/billing/subscription` | 200 即 key 有效；**不暴露余额和身份信息**，可安全用于连通性自检 |

鉴权：`Authorization: Bearer <key>`。

## 二、请求体

```jsonc
{
  "model": "sd-2.5",
  "prompt": "……",
  "reference_images": ["https://公网可访问URL"],  // 图生视频；纯文生视频可省略
  "duration": 29,                                  // 秒，注意不是 seconds
  "size": "1280x720"                               // 宽x高
}
```

- **时长字段是 `duration`**，`seconds` 是 MiniMax H3 族的字段名；写错会得到
  `duration must be a positive number`。
- **`size` 可能被模型忽略**（`seedance-2.5s` 收 1920x1080 仍出 720p），
  `seedance2.5-a` 则会明确报 `resolution 1080p is not supported`。想知道某型号
  实际输出什么，跑一次看下载文件的分辨率。
- 参考图：优先公网 http(s) URL；部分型号支持 base64（单张 ≤32MB、整体 ≤256MB）。

## 三、任务状态与超时

- `status`：`queued` / `processing` → `completed`；终态失败为 `failed` / `error` / `cancelled`。
- 查询时返回 **409 = 任务未完成**，不是错误，脚本当作"继续等"处理。
- **排队时间远超想象**：480p/720p 的 10~30 秒长片，实测排队 13~22 分钟。
  `--timeout` 给到 2400，`HUAHUA_HTTP_TIMEOUT` 给到 600。
- **超时不等于失败**：任务仍在服务端跑，用同一个 `task_id` 续等即可。
  **千万不要重复提交**——重复提交会重复扣费。
- **成片只保留 48 小时**，之后 `GET /v1/videos/{id}` 返回 `task_not_exist`，必须及时落盘。

## 四、错误码对照

| 现象 | 含义 | 处理 |
|---|---|---|
| 500 / 502 / 503 `fail_to_fetch_task`、`upstream error` | 上游或该型号渠道瞬时故障 | 可恢复，退避后重试或换模型（失败不计费） |
| 503 `失败` + `/v1/video/generations` | 路径写错 | 改成 `/v1/videos` |
| 400 `duration must be a positive number` | 时长字段名不对或缺失 | 用 `duration` 并给正整数 |
| 400 `resolution 1080p is not supported` | 该型号不支持该分辨率 | 换型号或降分辨率 |
| 400 `xxx must contain public http/https URLs` | 参考图不是公网 URL | 先用 `upload` 子命令传图拿 URL |
| 401 | key 无效/未启用 | 回控制台确认 |
| 403（下载时） | 下载没带鉴权 | `HUAHUA_DOWNLOAD_AUTH=1` |
| 404 `task_not_exist` | 超过 48h 被回收，或 id 记错 | 只能重新生成 |
| 余额不足类提示 | 没充值 | 去控制台充值后再提交 |

## 五、计费

- **提交成功即计费，任务失败通常不计费/全额退款**（以控制台账单为准）。
- 只核对请求体时用 `--dry-run`：不提交、不出片、不花钱。
- 想控制成本，先短时长小额试：`--duration 5`，确认效果再上长片。

## 六、参考图没有公网 URL 时

聚合站侧没有通用文件上传端点，本机实测：

- 可用：`https://uguu.se/upload`（字段 `files[]`，响应 `files[0].url`，URL 有时效）
- 不可用：`0x0.st`（已停用上传）、`catbox.moe`、`api.imgur.com`、`telegra.ph`、
  `sm.ms`、`tmpfiles.org`、`transfer.sh`（本机网络不可达）

用内置 `upload` 子命令即可：`python hh_video.py upload a.png b.png`。

## 七、模型命名规律

`/v1/models` 拉到的型号大致分三族：

- `sd-*` / `seedance*`：Seedance / SD 系列，按分辨率与档位带后缀
  （`sd-2.5-720p-pro`、`sd-2.5-1080p-max`、`seedance2.5-720s`、`seedance2.5-a` 等）
- `minimax-h3-*` / `官方h3-*`：MiniMax H3 族，**时长字段是 `seconds` 不是 `duration`**
- 后缀含义：`1080p` / `720p` / `480p` 是输出上限，`plus` / `pro` / `ultra` / `max`
  是档位，`fast` / `mini` 是轻量版

同一时刻不是所有型号都有可用渠道（取决于你的分组），是否可用以提交是否报错为准。
