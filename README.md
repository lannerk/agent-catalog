# agent-catalog

Frey 与 PiEv 共用的目录仓：插件市场、精选 skills、MCP 服务器目录。**没有服务器**——客户端只读这里的 raw 文件（`https://raw.githubusercontent.com/lannerk/agent-catalog/main/<路径>`），GitHub Actions 每天把官方 MCP 注册表同步过来。

*The shared catalog of Frey and PiEv: plugin marketplaces, curated skills, and a daily mirror of the official MCP Registry. No server: clients read the raw files.*

| 路径 | 是什么 | 谁写 |
|---|---|---|
| `frey/marketplace.json` | Frey 的插件市场（Agent Plugins 1.0 插件；条目只用 Claude Code 与 Codex 市场共有的字段，所以两家也能读） | 人手工 |
| `piev/marketplace.json` | PiEv 的插件市场（同一格式） | 人手工 |
| `skills/index.json` | 在 Frey 里试过的 skills（Agent Skills 格式），每条带 `frey skills add` / `npx skills add` 认的来源 | 人手工 |
| `mcp/servers.json` ＋ `mcp/servers/<0-f>.json` | 官方 MCP 注册表的镜像：每个没被删除的服务器的最新版本，按 `sha256(名字)` 的首位分成 16 份，一行一个 | `sync.yml` 每天 |
| `mcp/curated.json` | 在 Frey 里跑通、核过的服务器，带连接器配置模板（不含凭据）；**只有这里的标「已验证」**，装镜像里的其他服务器要确认「来源未经验证」 | 人手工 |
| `catalog/models-facts.json` | Frey 的模型事实表（窗口、输出上限、价格、模态、自报评测），从我们审过的 models.dev fork（`lannerk/models.dev` 的 `dev`）生成；`frey models refresh` 读它，费用与积分只用这一份 | `models-facts.yml` 每周 |
| `catalog/decision-models.json` | Frey 的本地决策模型目录（T-0124，AD139）：llama.cpp 运行时钉的版本与各平台包的 SHA-256，每个型号的 GGUF 地址（钉提交号）、SHA-256、大小、内存、许可分级、分数（带尺子与出处）；`frey decision list --refresh` 读它，整份检查、比随包的新才用。**与 Frey 仓 `src/frey/configs/decision-models.json` 逐字节相同**（测试核）；改它先改 Frey 那份，由 `claude-outputs/developer/2026-11-07-T-0124-实录/make_catalog.py` 生成 | 人手工 |
| `*.schema.json` | 上面各文件的 JSON Schema | 人手工 |
| `scripts/sync_mcp.py` / `test_sync_mcp.py` | 同步脚本（只用标准库，装了 jsonschema 就按官方 schema 核每条）与它的测试 | 人手工 |
| `scripts/build_models_facts.sh` / `check_models_facts.py` | 生成事实表（用 fork 自己 `frey/` 下的生成器、预设、修正与 schema）与提交前的检查（合 schema、至少 10 个预设 500 个模型） | 人手工 |

## MCP 服务器镜像

- 来源：`https://registry.modelcontextprotocol.io/v0.1/servers`。第一次（或手动运行时选 `full`）全量；之后每天 `updated_since=<上次的开始时间>` 增量：一条记录是它那个服务器的最新版本、没被删，就替换；某个服务器有版本被删，就再问一次它的最新版本，没了就从镜像里删掉。
- 每条进镜像前先核：客户端要读的字段在、`$schema` 指的官方 schema 过得了；过不了的不收、在运行日志里列名字。
- 服务器没变就不提交。
- 客户端怎么读：先读 `mcp/servers.json`（`count`、每份的 `count` 与 `sha256`），再按需读分片；按 `sha256` 判断分片变没变、要不要重新下。
- 官方注册表只核了「命名空间归谁」，没核代码安全：镜像只用来**发现**，装什么由人决定。

## 模型事实表

- 链路：上游 `anomalyco/models.dev` → 我们的 fork `lannerk/models.dev`（每周 `frey-sync` 开 PR：价格变动超过 30%、模型被删、新模型列在描述里，人看过再合进 `dev`；我们的修正只放 fork 的 `frey/overrides/`）→ 这里每周 `models-facts.yml` 从 fork 的 `dev` 生成 `catalog/models-facts.json` → 客户端 `frey models refresh`（或打开 `models.auto_refresh`）。
- 手动跑一遍：`git clone -b dev https://github.com/lannerk/models.dev .fork && bash scripts/build_models_facts.sh .fork`（要装 jsonschema）。

## 加一条

- **skill**：在 Frey 里 `frey skills add <来源>` 装上、真用一次，再往 `skills/index.json` 加一条（`verified` 写日期、Frey 版本、提交号）。
- **MCP 服务器**：在 Frey 里 `frey connectors add <注册表名>` 跑通（工具列得出来、调一个工具成功）后，往 `mcp/curated.json` 加一条，`connector` 照 Frey 写进 `~/.frey/connectors.toml` 的那一段抄（凭据不写）。
- **插件**：插件包过 `strict` 校验、在 Frey 里 `frey plugins add` 装上真用一次后，往对应的 `marketplace.json` 加一条（`name`、`source`、`description`、`version`、`category`、`policy`；要置顶加 `tags: ["recommended"]`）。`frey/marketplace.json` 里的条目就是 Frey 的**精选**：装时不带「来源未验证」，`plugins.allow_third_party` 关着也能装（T-0132）。别人仓里的插件用 `git-subdir` 钉 `sha` 指过去，不拷进本仓。

改完本地跑一次 `python3 scripts/test_sync_mcp.py`（装了 jsonschema 才会核 schema）。

## 维护

这个仓的骨架在 Frey 的私有仓 `ops/release/agent-catalog/`，在那里改、再推到这里（`mcp/servers*` 由 Actions 生成，不在骨架里）。
