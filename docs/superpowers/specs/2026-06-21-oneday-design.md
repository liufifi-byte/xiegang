# oneday — 每日一句话日记 CLI（设计文档）

- 日期：2026-06-21
- 状态：已设计，待用户审阅
- 范围：最小可用——仅"写"功能

## 1. 目标

在终端用一条命令记录每天的一句话日记。同一天再写则覆盖当天记录。本阶段只实现"写"功能，读/列表/查日期不在范围内。

## 2. 技术栈与方案

- 语言：Python，单文件 `oneday.py`，零第三方依赖。
- 参数解析：`sys.argv` 直接取第一个参数（方案 A）。不引入 argparse/click，避免对"只写"范围过度设计。

## 3. 文件布局

```
/Users/xinyiliu/ZCodeProject/
├── oneday.py                                              # 唯一源文件
├── test_oneday.py                                         # 测试
└── docs/superpowers/specs/2026-06-21-oneday-design.md     # 本文档
```

数据文件位于用户家目录：`~/.oneday/log.json`，不放入代码仓库。

## 4. 命令接口

```
python oneday.py "<一句话正文>"
```

| 输入 | 行为 | 退出码 | 输出流 |
|------|------|--------|--------|
| `python oneday.py "正文"` | 写入当天记录 | 0 | stdout 无输出（沉默成功） |
| 无参数 | `usage: python oneday.py "<text>"` | 2 | stderr |
| 空字符串 `""` | `error: text must not be empty` | 2 | stderr |
| 多余参数 `python oneday.py "a" "b"` | 只取第一个，忽略其余 | 0 | — |

要点：
- 同一天再写则覆盖（JSON 以日期为键，天然幂等）。
- 退出码遵循 Unix 惯例：0 成功，2 用法错误。
- 不做 `--help`、不做子命令。

## 5. 数据结构与存储

存储位置：`~/.oneday/log.json`

结构（以日期字符串为键，一天一条）：

```json
{
  "2026-06-21": {
    "text": "今天学到了 brainstorming 流程",
    "created_at": "2026-06-21T15:30:00+08:00"
  }
}
```

字段：
- 键：本地日期 `YYYY-MM-DD`，由 `datetime.date.today().isoformat()` 生成。
- `text`：用户输入原文，原样存储，不转义/不截断。
- `created_at`：写入时刻的 ISO 8601 带时区，由 `datetime.datetime.now().astimezone().isoformat()` 生成。当天覆盖时此字段更新为最新一次。

读写流程：
1. 读：`json.loads(log_path.read_text())`；文件不存在则视为 `{}`；文件存在但内容为空（0 字节或仅空白）也视为 `{}`；文件存在且非空但 `json.loads` 失败则为"损坏"，按第 6 节报错。
2. 改：`data[today] = {"text": ..., "created_at": ...}`。
3. 写：`log_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))`。

首次运行：写入前 `log_path.parent.mkdir(parents=True, exist_ok=True)` 确保目录存在；读取处用 `if log_path.exists()` 守卫，不存在即当 `{}`。

## 6. 错误处理

| 场景 | 处理 | 退出码 | 输出流 |
|------|------|--------|--------|
| 无参数 | `usage: python oneday.py "<text>"` | 2 | stderr |
| 空字符串 | `error: text must not be empty` | 2 | stderr |
| 无法创建数据目录（权限） | `error: cannot create data directory: <原因>` | 1 | stderr |
| `log.json` 内容损坏（非法 JSON） | `error: log file corrupted: <原因>` | 1 | stderr |
| 写入磁盘失败 | `error: failed to write: <原因>` | 1 | stderr |

要点：
- 退出码区分两类：`2` = 调用方用法错误；`1` = 运行时环境错误。
- 错误信息为英文短句 + 原因，写 stderr，不抛 traceback。
- 正常成功时 stdout 无输出（Unix 管道友好）。

## 7. 测试

框架：纯标准库 `unittest`，零测试依赖。

测试用例：

| 用例 | 做法 | 预期 |
|------|------|------|
| 正常写入 | 临时目录作 `~/.oneday`，写一条 | `log.json` 出现，键=今天，`text` 正确 |
| 同日覆盖 | 同一天写两次 | 只剩一条，`text` 为第二次值，`created_at` 更新 |
| 中文正文 | 写 `"今天学到了流程"` | JSON 中中文原样（`ensure_ascii=False`）|
| 无参数 | `sys.argv = ["oneday.py"]` | 退出码 2，stderr 有 usage |
| 空字符串 | `sys.argv = ["oneday.py", ""]` | 退出码 2，stderr 有 error |
| 首次运行 | 数据目录不存在 | 自动创建目录并写入成功 |

技巧：
- 用 `unittest.mock.patch` 把数据路径指向 `tempfile.TemporaryDirectory()`，每用例隔离，不污染真实 `~/.oneday`。
- mock `datetime.date.today`，保证"同日覆盖"用例跨天稳定。
- 运行：`python -m unittest test_oneday.py`。

不在范围内：读/列表/查日期的测试、`--help` 测试、多参数处理测试。

## 8. 不在本次范围（明确排除）

- 读最近 N 条 / 查指定日期 / 列表展示。
- `--help`、子命令、argparse 框架。
- 编辑/删除历史记录。
- 多设备同步、加密、导出。
