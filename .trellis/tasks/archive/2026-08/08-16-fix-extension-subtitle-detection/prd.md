# 修复浏览器插件字幕识别兼容性

## Goal

修复 Notebook Agent 浏览器伴侣在受支持视频页面上经常把已有字幕识别为“无字幕”的问题，使 YouTube 与 NTULearn/Kaltura 的字幕发现、解析和跨 Frame 选择具有明确、可测试、可诊断的行为。

## User-visible scope

- 支持当前产品声明的 YouTube watch/short-link 视频页面与 NTULearn/NTULearnVideo 中的 Kaltura 视频上下文。
- 用户点击一次“保存当前视频”后，插件应优先返回可读取的完整字幕；只有在所有受支持发现路径都确认没有可用 cue 时才返回 `caption.status=unavailable`。
- 页面结构、播放器状态或单个 Frame/字幕候选异常时，插件应继续尝试安全的回退路径，并在全部路径失败时返回可恢复的稳定错误，而不是误报成功或静默吞掉关键失败。

## Requirements

### R1. Robust page and frame orchestration

- YouTube 与 NTULearn/Kaltura 必须使用清晰分离的平台适配器；平台判断只接受显式支持的 HTTPS host/page shape。
- NTULearn/Kaltura 的外层页面、`ntulearnv1.ntu.edu.sg` 实际媒体页、既有 NTULearnVideo 页面和受许可的 Kaltura 播放器 Frame 都要参与发现；单个 Frame 没有 entry id、没有字幕或执行失败不得阻止其他 Frame 的有效结果。
- 多个结果必须按“有效字幕优先，其次媒体身份与元数据完整度”确定性选择，并避免把通用 Media Gallery/Kaltura 标题当成更好的结果。
- YouTube 单页应用导航后不得使用与当前 URL 视频 ID 不一致的陈旧 player response。

### R2. Complete caption discovery and parsing

- YouTube 应从当前播放器可用的响应来源发现 caption tracks，优先选择当前播放器已选轨或响应声明的有效默认轨；没有有效偏好时选择人工字幕优先、自动字幕回退，并读取规范化 timed cues。
- YouTube 的可信签名字幕端点若不再返回有效 JSON3，必须在同一受信 URL 上回退到 WebVTT 或原始 timed-text XML；单一表示失败不能让页面已有的可见字幕整体失败。
- 若所有 timed-text 表示为空但页面公开了官方 transcript endpoint，扩展可在 YouTube 同源内调用该端点；不透明参数必须验证绑定当前视频 ID，响应只提取规范化 cues，API key、上下文、参数和响应正文不得进入 Notebook Agent 或日志。
- NTULearn/Kaltura 应覆盖浏览器原生 `TextTrack`、DOM `<track>`、当前 Frame 已加载的可信字幕资源，以及播放器暴露的可验证字幕描述；一个候选失败后继续尝试下一个。
- 支持当前产品范围内实际出现的 JSON3、WebVTT、SRT 与 TTML/DFXP 时间文本；规范化空白/简单标记，拒绝非有限或反向时间、空文本和不可解析内容。
- 字幕语言应来自选中 track/播放器描述；无法确定时使用 `und`，不能把页面 UI 语言冒充为已知字幕语言。

### R3. Security and privacy invariants

- 只在当前授权页面/Frame 内消费字幕资源，不把 Cookie、Authorization、SAML、Kaltura KS、签名参数、字幕/播放资源 URL 或页面异常正文提交给 Notebook Agent。
- 资源候选必须限制为 HTTPS 和受支持的 NTU/Kaltura/YouTube 字幕来源；非受信 host、非字幕形状 URL 和 malformed URL 必须忽略。
- 返回对象继续满足现有 `capture.v1` 契约和大小/字段限制；本任务不扩大插件权限到音视频捕获或通用网络监听。

### R4. Actionable failure semantics

- “确实没有可读取字幕”与“页面/字幕请求失败”必须可区分：前者进入现有 `needs_asr` 流程，后者显示可重试的字幕读取错误。
- 适配器或 Frame 内部错误不得携带页面秘密进入日志、popup 文案或提交 payload。
- 不改变现有配对、租户隔离、幂等提交与后端摄取语义。

### R5. Complete regression coverage

- 扩展测试必须覆盖平台路由、Frame 编排、候选排序、YouTube 人工/自动字幕与 SPA 陈旧响应、Kaltura 原生 TextTrack 与资源回退、JSON3/VTT/SRT/TTML 解析、无字幕、请求/解析失败、候选继续、URL 信任边界和敏感数据不外泄。
- 测试必须断言正向结果、基础无字幕结果和失败结果，而不只检查函数是否返回。
- 必须运行扩展的 unit tests、strict TypeScript、ESLint、production build 和权限/产物 audit；相关后端 capture 契约测试也必须通过，证明 `capture.v1` 未回归。
- 测试工具链应可从仓库声明的命令重复运行；若引入覆盖率门槛，必须锁定兼容依赖并对字幕识别核心模块设置有意义的 statements/branches/functions/lines 阈值。

### R6. Quantified real-video validation

- 使用最终构建的 unpacked extension 在真实浏览器页面中验证至少 5 个不同 YouTube 视频和 2 个不同 NTULearn/Kaltura 视频；7 个视频必须全部识别到非空字幕，成功率门槛为 `7/7 = 100%`。
- YouTube 样本应覆盖人工字幕与自动字幕、至少两种字幕语言，并至少包含一次同标签页 SPA 导航后的捕获；不得用同一视频的不同 URL 或不同语言轨重复计数。
- Kaltura 样本必须是两个不同 entry id，并尽量覆盖 NTULearn/NTULearnVideo 的外层媒体页与嵌入播放器两种入口；两个样本都必须通过用户已有授权正常播放且确有字幕。
- 每个真实视频记录平台、去敏视频标识、入口类型、字幕类型/语言、cue 数、第一条开始时间、最后一条结束时间、播放器时长、字幕时间覆盖率、捕获耗时、最终状态和人工抽查结果。
- 字幕时间覆盖率定义为 `(最后 cue.end_sec - 第一 cue.start_sec) / 播放器 duration_sec`；仅作为异常检测指标，不要求字幕覆盖片头/片尾静音。若覆盖率低于 80%，必须人工核对开头/中间/结尾 cue，说明是内容本身留白还是抓取不完整。
- 每个视频至少抽查开头、中间、结尾各一个 cue 与播放器可见字幕一致；不得仅以 `cue_count > 0` 判定成功。
- 真实验证记录不得包含完整私有标题、课程名、Cookie、Authorization、SAML、KS、签名资源 URL 或完整字幕正文。Kaltura 记录只保留脱敏 entry id、数值指标和安全结论。

### R7. Deterministic local qualification target

- 扩展必须能生成互斥的生产与本地构建：生产版只连接 `https://notebookai.deequoique.tech`，本地版 API 只连接精确回环地址 `http://127.0.0.1:8000`，同一产物不得同时持有两个 API host permission。配对批准页面继续使用 `https://localhost:8443` 的安全 Web origin。
- API 请求必须有有界超时并返回稳定安全错误，服务不可达时 popup 不得无限转圈。
- 本地构建必须通过与生产构建相同的类型、lint、测试和精确权限审计，并能向本地配对端点创建请求、得到本地批准地址。

## Acceptance Criteria

- [ ] 有字幕的受支持 YouTube 页面可从当前视频响应获取 cues；当前/默认轨优先，没有有效轨偏好时人工字幕优先，只有人工字幕不可用时才选择自动字幕。
- [x] YouTube JSON3 为空或非 JSON 时会继续尝试可信 WebVTT 与原始 XML，并有成功回归测试；失败响应或不可信重定向仍不能绕过 host 边界。
- [x] YouTube timed-text 全部为空时会回退到当前视频绑定的官方 transcript endpoint；当前视频成功与旧视频参数拒绝都有直接测试。
- [ ] YouTube SPA 导航后，当前 URL 与 player response 不匹配时不会提交旧视频字幕，并会使用当前播放器的有效响应或返回稳定错误。
- [ ] 有字幕的 NTULearn/Kaltura 视频可从外层页或任一受许可播放器 Frame 获取 cues；单个 Frame 失败不会让整个捕获失败。
- [x] 真实使用的 `https://ntulearnv1.ntu.edu.sg/*` 媒体页被 manifest、扩展平台路由和后端 `page_url` 契约精确允许；不扩大为 `*.ntu.edu.sg` 通配权限。
- [ ] 原生 TextTrack、可信 VTT/SRT、TTML/DFXP 各至少有一个自动化成功用例；损坏格式、HTTP 失败与第一个候选失败后的回退均有用例。
- [ ] 所有发现路径确认无字幕时返回 `unavailable`/`needs_asr`；发现到字幕描述但读取失败时返回可重试错误，不误报“无字幕”。
- [ ] 捕获结果、错误和测试快照均不包含 KS、签名字幕 URL、Cookie、Authorization 或 SAML 材料。
- [ ] 平台路由、Frame 调用参数和最佳结果选择有直接单元测试，覆盖 top frame、allFrames、空结果、部分错误与多结果竞争。
- [ ] 扩展测试、类型检查、lint、build、package audit 与相关后端 capture 回归测试全部通过；测试报告记录命令和结果。
- [x] 最终 unpacked extension 对 5 个不同且有字幕的 YouTube 视频全部识别成功（`5/5`），包含人工/自动字幕与一次 SPA 导航；第二语言选择未保留为可审计证据，按用户 2026-08-17 的明确归档指示记录为测试计划豁免。
- [x] 最终 unpacked extension 对 2 个不同 entry id 且有字幕的授权 NTULearn/Kaltura 视频全部识别成功（`2/2`），两条均完成真实登录态抓取与后台整理。
- [x] 7 个真实视频已记录或安全复算 cue 数、语言与时间覆盖率并通过敏感信息审查；popup 未持久化捕获耗时/字幕正文，缺失遥测按用户明确归档指示记录为关闭豁免。
- [x] 生产/本地构建只包含各自唯一的精确 API host permission；本地版 API 连接 `http://127.0.0.1:8000` 并保留 HTTPS 批准页，生产版行为不变。
- [x] 扩展 API 请求在 10 秒内超时并显示稳定错误；网络失败、超时、服务端错误和非法/歧义 API permission 均有单元测试。

## Constraints

- 工作区已有未提交的浏览器伴侣修复和其他用户改动；实现必须在其上增量修改，不能重置、覆盖或把无关改动据为本任务成果。
- 不绕过登录、MFA、访问控制或 DRM，不引入音视频上传/ASR。
- 不通过广泛 host permission 或通用请求监听来换取兼容性。

## Out of Scope

- 新增受支持平台。
- 浏览器商店发布。
- 音频/视频捕获、上传或服务端 ASR 实现。
- 重构配对、后端租户模型或 Web 资料库 UI。
