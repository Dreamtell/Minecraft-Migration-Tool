# Python 性能处理库总结（供 AI 解析，仅性能）

> 这份是通用速查表，**不是本项目的优化指南**。
> 本项目（Tk 主进程 + PySide6 子进程的 Windows 桌面工具）到底哪几条适用，
> 见文末「本项目适用性」一节 —— 照那份看，别照上面照抄。

## 1. 核心原则
- **先剖析，后优化**：先定位瓶颈类型，再选库。
- 瓶颈类型：`CPU计算密集` / `I/O等待` / `多核并行` / `数据框与SQL` / `序列化` / `内存与小对象`。

## 2. 性能剖析工具
| 类别 | 工具 | 作用 | 适用场景 |
|---|---|---|---|
| 函数级剖析 | `cProfile` | 统计函数调用次数与累计耗时 | 快速定位热点函数 |
| 采样剖析 | `py-spy` | 非侵入式附着运行进程，生成火焰图 | 线上服务、不能中断运行 |
| 行级剖析 | `line_profiler` | 精确到代码行耗时 | 热点函数内部哪一行慢 |
| 内存剖析 | `memory_profiler` | 逐行内存使用分析 | 定位内存分配热点 |

## 3. 执行加速：JIT/AOT 编译
| 工具 | 类型 | 核心作用 | 适用瓶颈 |
|---|---|---|---|
| `Numba` | JIT 编译器 | `@jit` 将数值函数编译为机器码 | NumPy 数组、紧循环、计算密集 |
| `PyPy` | 替代解释器 | 内置 JIT，整体加速长期运行程序 | 纯 Python 长期运行，兼容性需验证 |
| `Cython` | AOT 编译 | 静态类型声明，编译为 C 扩展 | 对关键模块精细优化 |
| `Codon` | 高性能编译器 | 编译加速 Python | 追求编译级性能 |
| `agentic-compiler` | 自动剖析+热替换 | 自动尝试 Numba/Rust/CUDA 后端，失败回滚 | 降低手动编译门槛 |

## 4. 并发与并行
| 工具 | 类型 | 核心作用 | 适用瓶颈 |
|---|---|---|---|
| `concurrent.futures.ProcessPoolExecutor` | 多进程 | 绕过 GIL，利用多核 | CPU 密集型、可分解任务 |
| `multiprocessing` | 多进程 | 创建独立进程并行 | CPU 密集型 |
| `asyncio` | 异步 I/O | 单线程事件循环处理并发 I/O | 网络、磁盘等 I/O 等待 |
| `uvloop` | asyncio 事件循环替代 | 基于 libuv，提升事件循环速度 | 高并发网络服务，I/O 密集 |
| `threading` / `ThreadPoolExecutor` | 多线程 | I/O 等待期间释放 GIL | 简单 I/O 并发 |

## 5. 数据框与 SQL 分析
| 工具 | 核心作用 | 适用场景 |
|---|---|---|
| `Polars` | Rust 编写，惰性求值，高性能数据框 | 复杂变换、过滤、大数据集 |
| `DuckDB` | 进程内 OLAP 数据库 | 多表 Join、重度聚合、SQL 分析 |
| `Dask` | 分块并行处理超内存数据集 | 单机内存不足、多核/分布式 |

经验：**复杂变换和过滤优先 Polars；多表 Join 和重度聚合优先 DuckDB。**

## 6. 序列化
| 工具 | 核心作用 | 适用场景 |
|---|---|---|
| `orjson` | 极快 JSON 编解码，Rust 编写 | 替代标准库 `json` 的性能关键场景 |
| `msgspec` | 高速解码 JSON 为带类型结构化对象，含模式校验 | 需要高性能解码 + 类型校验 |

## 7. 内存与小对象
| 工具/机制 | 核心作用 | 适用场景 |
|---|---|---|
| `__slots__` | 禁止实例创建 `__dict__`，降低单对象内存 | 海量小对象、GC 压力大 |
| `array.array` | C 风格紧凑存储同类型数值 | 大量同类型数值，替代 list |
| `pyzgc` | 并发压缩式 GC 实验原型 | 减少 GC 暂停，**实验/研究，不建议生产** |

## 8. AI 选择路径
1. 用 `cProfile` / `py-spy` 定位瓶颈。
2. CPU 密集纯 Python → `Numba`（函数级）或 `PyPy`（整体）。
3. I/O 密集 → `uvloop` 加速 `asyncio`；简单同步用线程池。
4. CPU 密集且可并行 → `ProcessPoolExecutor` / `multiprocessing`。
5. 数据框/SQL 慢 → `Polars` 或 `DuckDB`；超内存 → `Dask`。
6. 序列化开销大 → `orjson` 或 `msgspec`。
7. 海量小对象内存问题 → `__slots__` / `array.array`。

## 9. 标注
- 标准库内置：`cProfile`、`concurrent.futures`、`multiprocessing`、`asyncio`、`threading`、`__slots__`、`array.array`。
- 第三方需安装：`py-spy`、`line_profiler`、`memory_profiler`、`Numba`、`PyPy`、`Cython`、`Codon`、`agentic-compiler`、`uvloop`、`Polars`、`DuckDB`、`Dask`、`orjson`、`msgspec`、`pyzgc`。

---

# 本项目适用性（写给后来的人 / 其它 AI）

本项目的形态：**Tk 主进程 + PySide6 子进程的 Windows 桌面工具**，负载是
文件复制、HTTP 请求、Tk 重绘、上百个 jar 的 `zipfile` 解析。
历史上的"卡顿/未响应"全部来自 **Tk 重绘被按住** 和 **worker 线程占 GIL**，
没有一处是算法复杂度问题 —— 所以上面那张表里绝大多数条目对本项目没用。

## 真的能用上的

| 项 | 怎么用 |
|---|---|
| `cProfile` | 量过：`index_modids` 处理真机 430 个 jar 约 1.3 秒（开剖析 2.7 秒），热点是 `zipfile` 解析中央目录（`BytesIO.read` 139 万次、`bytes.decode` 35 万次），**不是 Python 代码本身**。换库没用，靠缓存（已有的 `_MODID_CACHE`）就够。 |
| `py-spy` | 最有价值的一条：本项目遇到"界面卡住"时，现在靠 `utils/hangwatch.py`（`faulthandler` 转储到 `~/.minecraft_migrate_hang.log`）。`py-spy` 能附着到已经卡死的进程直接看栈/火焰图，不用先埋点。**只作开发工具，不进发行包。** |
| `threading` | 已在用：迁移/回滚跑在 worker 线程，UI 更新一律 `_ui_post` 回主线程（Tk 不是线程安全的）。这条路早就是对的。 |

## 有条件才考虑

| 项 | 条件 |
|---|---|
| `ProcessPoolExecutor` / `multiprocessing` | 只有当 jar 数量涨到几千、且实测"解析"成为用户可感知的等待时才值得。当前 430 个约 1.3 秒、跑在后台线程，**并行反而更慢**（实测：每个 jar 约 3ms，进程创建+序列化的开销吃掉收益；让出 GIL 也无收益）。 |

## 明确不适用（别装）

| 项 | 原因 |
|---|---|
| `Numba` / `Cython` / `Codon` / `agentic-compiler` | 没有数值热循环；给 Tk 桌面程序加编译链是纯负担（还要处理打包）。 |
| `PyPy` | 项目大量用 `ctypes` 直调 Win32（`SetWindowPos` / `SetWindowRgn` / DWM 圆角 / `UpdateLayeredWindow`），并要跑 PySide6 子进程，PyPy 上跑不通。 |
| `uvloop` | **这份清单这里是错的**：uvloop 不支持 Windows（依赖 libuv 的 Unix 事件循环）。而且本项目主循环是 Tk 的 `mainloop()`，根本不是 asyncio。 |
| `Polars` / `DuckDB` / `Dask` | 项目里没有 dataframe；迁移历史只是几十条 JSON。 |
| `orjson` / `msgspec` | 联网搜索单次响应约 10 KB，解析占比 < 1%。 |
| `__slots__` / `array.array` | 常驻对象是几百个模组条目，谈不上"海量小对象"。 |

## 一句话

这份清单面向"服务端 / 数据管道"。本项目要治的是 **Tk 事件循环** 和 **GIL 占用**，
先看 `docs/HANDOFF.md` 里的动画与线程约定，再看这份表。
