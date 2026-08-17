import { useState } from "react";

import { BrandLogo } from "../app/BrandLogo";
import { BROWSER_COMPANION_DOWNLOAD_URL } from "../app/browserCompanion";
import { RouteLink } from "../app/RouteTransition";

type DemoId = "product" | "ai" | "practice";
type SummaryLanguage = "zh" | "en";

interface EvidenceLink {
  label: string;
  note: string;
  url: string;
}

interface DemoScene {
  id: DemoId;
  index: string;
  audience: string;
  title: string;
  question: string;
  sourceTitle: string;
  sourceCreator: string;
  sourceUrl: string;
  thumbnailUrl: string;
  previewSummary: Record<SummaryLanguage, string>;
  summaryDurationSeconds: Record<SummaryLanguage, number>;
  answerLead: string;
  answerPoints: string[];
  evidence: EvidenceLink[];
}

const demoScenes: DemoScene[] = [
  {
    id: "product",
    index: "01",
    audience: "产品调研",
    title: "从礼貌反馈里，找出真实需求",
    question: "我该怎样采访早期用户，才不会只得到礼貌性的肯定？",
    sourceTitle: "How to Talk to Users",
    sourceCreator: "Eric Migicovsky · Y Combinator",
    sourceUrl: "https://www.youtube.com/watch?v=MT4Ig2uqjTc",
    thumbnailUrl: "https://i.ytimg.com/vi/MT4Ig2uqjTc/hqdefault.jpg",
    previewSummary: {
      zh: "先从最近一次真实经历问起：最困难的环节是什么、目前怎样解决，以及是否已经为替代方案付出时间或金钱。",
      en: "Start with a recent real experience: ask what was hardest, how it was handled, and whether the person already sought an alternative.",
    },
    summaryDurationSeconds: { zh: 36, en: 42 },
    answerLead: "不要先推销方案，也不要让用户预测未来。把对话拉回到已经发生过的具体经历。",
    answerPoints: [
      "先问最近一次遇到问题的时间、地点和上下文，而不是“你会不会用这个功能”。",
      "观察对方是否已经花时间或金钱寻找替代方案，用行动判断痛点强度。",
      "追问现有方案哪里不好，从具体缺口中提炼功能和产品表达。",
    ],
    evidence: [
      {
        label: "06:07",
        note: "从最难的具体环节开始提问",
        url: "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=367s",
      },
      {
        label: "08:16",
        note: "追问最近一次真实经历",
        url: "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=496s",
      },
      {
        label: "11:17",
        note: "检查用户是否主动找过解决办法",
        url: "https://www.youtube.com/watch?v=MT4Ig2uqjTc&t=677s",
      },
    ],
  },
  {
    id: "ai",
    index: "02",
    audience: "AI 入门",
    title: "把抽象概念，拆成能复述的结构",
    question: "用高中生能理解的方式解释：神经网络到底在做什么？",
    sourceTitle: "But what is a neural network?",
    sourceCreator: "Grant Sanderson · 3Blue1Brown",
    sourceUrl: "https://www.youtube.com/watch?v=aircAruvnKk",
    thumbnailUrl: "https://i.ytimg.com/vi/aircAruvnKk/hqdefault.jpg",
    previewSummary: {
      zh: "把手写数字图片看成 784 个亮度输入；网络逐层组合这些数字，最后给出 10 个数字类别的判断。",
      en: "Treat a 28×28 image as 784 brightness inputs; layered transformations combine them into scores for ten digit classes.",
    },
    summaryDurationSeconds: { zh: 34, en: 40 },
    answerLead: "可以先把它理解成一个会调参数的数字转换器：输入很多数字，经过多层变换，输出一组判断结果。",
    answerPoints: [
      "示例把一张 28×28 的手写数字图片转换成 784 个亮度值，每个值进入一个输入神经元。",
      "中间层根据权重和偏置计算新的激活值，让像素逐层组合成边缘、形状等更高层特征。",
      "整个网络本质上是一个从 784 个输入到 10 个输出的函数；训练就是不断调整其中的参数。",
    ],
    evidence: [
      {
        label: "03:08",
        note: "28×28 像素如何成为 784 个输入",
        url: "https://www.youtube.com/watch?v=aircAruvnKk&t=188s",
      },
      {
        label: "04:03",
        note: "隐藏层与逐层激活",
        url: "https://www.youtube.com/watch?v=aircAruvnKk&t=243s",
      },
      {
        label: "15:39",
        note: "把整个网络理解成一个函数",
        url: "https://www.youtube.com/watch?v=aircAruvnKk&t=939s",
      },
    ],
  },
  {
    id: "practice",
    index: "03",
    audience: "技能训练",
    title: "把“多练”变成“有效地练”",
    question: "怎样让技能练习更有效，而不是机械重复？",
    sourceTitle: "How to practice effectively...for just about anything",
    sourceCreator: "Annie Bosler & Don Greene · TED-Ed",
    sourceUrl: "https://www.youtube.com/watch?v=f2O6mQkFiiw",
    thumbnailUrl: "https://i.ytimg.com/vi/f2O6mQkFiiw/hqdefault.jpg",
    previewSummary: {
      zh: "有效练习重在专注、挑战能力边缘、慢速建立正确动作，并通过高质量重复与休息持续修正。",
      en: "Effective practice uses focused work near the edge of ability, slow correct repetitions, feedback, and planned breaks.",
    },
    summaryDurationSeconds: { zh: 32, en: 38 },
    answerLead: "有效练习不只看时长，更看注意力、难度边界和反馈质量。",
    answerPoints: [
      "练习时减少干扰，把注意力集中在当前任务和最薄弱的环节上。",
      "新动作先放慢，优先建立正确且高质量的重复，再逐渐提高速度。",
      "把练习拆成多次短时段并安排休息；动作已经建立后，也可以用清晰的心理演练巩固。",
    ],
    evidence: [
      {
        label: "02:26",
        note: "持续、专注且贴近能力边界",
        url: "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=146s",
      },
      {
        label: "03:05",
        note: "先慢速练出正确动作",
        url: "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=185s",
      },
      {
        label: "03:18",
        note: "高频重复与间隔休息",
        url: "https://www.youtube.com/watch?v=f2O6mQkFiiw&t=198s",
      },
    ],
  },
];

type AudienceIconName = "learner" | "research" | "creator" | "channels";

const audienceIconPaths: Record<AudienceIconName, string[]> = {
  learner: [
    "M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0",
    "M6 21v-2a4 4 0 0 1 4 -4h.5",
    "M17.8 20.817l-2.172 1.138a.392 .392 0 0 1 -.568 -.41l.415 -2.411l-1.757 -1.707a.389 .389 0 0 1 .217 -.665l2.428 -.352l1.086 -2.193a.392 .392 0 0 1 .702 0l1.086 2.193l2.428 .352a.39 .39 0 0 1 .217 .665l-1.757 1.707l.414 2.41a.39 .39 0 0 1 -.567 .411l-2.172 -1.138",
  ],
  research: [
    "M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0",
    "M6 21v-2a4 4 0 0 1 4 -4h1.5",
    "M15 18a3 3 0 1 0 6 0a3 3 0 1 0 -6 0",
    "M20.2 20.2l1.8 1.8",
  ],
  creator: [
    "M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0",
    "M6 21v-2a4 4 0 0 1 4 -4h3.5",
    "M18.42 15.61a2.1 2.1 0 0 1 2.97 2.97l-3.39 3.42h-3v-3l3.42 -3.39",
  ],
  channels: [
    "M10 13a2 2 0 1 0 4 0a2 2 0 0 0 -4 0",
    "M8 21v-1a2 2 0 0 1 2 -2h4a2 2 0 0 1 2 2v1",
    "M15 5a2 2 0 1 0 4 0a2 2 0 0 0 -4 0",
    "M17 10h2a2 2 0 0 1 2 2v1",
    "M5 5a2 2 0 1 0 4 0a2 2 0 0 0 -4 0",
    "M3 13v-1a2 2 0 0 1 2 -2h2",
  ],
};

function AudienceIcon({ name }: { name: AudienceIconName }) {
  return (
    <span className="showcase-audience__icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {audienceIconPaths[name].map((path) => <path key={path} d={path} />)}
      </svg>
    </span>
  );
}

const audiences: Array<{ icon: AudienceIconName; title: string; copy: string }> = [
  {
    icon: "learner",
    title: "深度学习者",
    copy: "收藏了大量课程、访谈和演讲，希望按问题重新调取，而不是从头重看。",
  },
  {
    icon: "research",
    title: "研究与产品团队",
    copy: "需要跨视频比对观点，并把每个结论快速定位回原始上下文。",
  },
  {
    icon: "creator",
    title: "创作者与知识工作者",
    copy: "想把看过的内容变成可复用素材，同时保留标题、片段与时间戳。",
  },
  {
    icon: "channels",
    title: "跨平台收集",
    copy: "如果部署启用了多个聊天入口，绑定后的账号可以共用一份私人资料库，并通过已启用的可信入口完成 Web 登录。",
  },
];

const processSteps = [
  {
    index: "01",
    title: "提交并归档视频来源",
    copy: "通过已启用的聊天入口或 Web 资料库保存 YouTube 链接，并补充保存理由或预期用途，便于后续识别与筛选。",
    output: "视频来源 + 保存说明",
  },
  {
    index: "02",
    title: "异步解析并建立索引",
    copy: "系统在后台提取视频标题、章节与字幕，将长内容切分为可检索片段，并保留片段与原视频之间的对应关系。",
    output: "字幕与章节 → 内容索引",
  },
  {
    index: "03",
    title: "在个人资料库中检索",
    copy: "用户可以直接用自然语言描述问题；系统只在当前账户的资料库范围内定位相关片段，并组织回答所需的上下文。",
    output: "自然语言问题 → 相关原文",
  },
  {
    index: "04",
    title: "生成带来源依据的回答",
    copy: "回答基于检索到的原文片段生成，并附带视频标题、引用摘录和可跳转时间点，方便回到完整语境核对。",
    output: "回答依据 + 原视频时间点",
  },
];

function ArrowIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M5 12h13M13 6l6 6-6 6" />
    </svg>
  );
}

function TypewriterText({ text }: { text: string }) {
  return (
    <>
      <span className="sr-only">{text}</span>
      <span className="demo-typewriter" aria-hidden="true">{text}</span>
    </>
  );
}

export function ShowcasePage() {
  const [activeDemoId, setActiveDemoId] = useState<DemoId>("product");
  const [heroCoverId, setHeroCoverId] = useState<DemoId | null>(null);
  const [hasRun, setHasRun] = useState(false);
  const [summaryLanguage, setSummaryLanguage] = useState<SummaryLanguage>("zh");
  const activeDemo = demoScenes.find((scene) => scene.id === activeDemoId) ?? demoScenes[0];
  const activeSummary = activeDemo.previewSummary[summaryLanguage];
  const summaryDuration = activeDemo.summaryDurationSeconds[summaryLanguage];
  function selectDemo(id: DemoId) {
    setActiveDemoId(id);
    setHasRun(false);
  }

  return (
    <div className="showcase-page">
      <a className="showcase-skip" href="#showcase-main">跳到主要内容</a>

      <header className="showcase-nav">
        <RouteLink className="showcase-brand" to="/" aria-label="Notebook Agent 首页">
          <BrandLogo className="showcase-brand__mark" />
          <span>NOTEBOOK / AGENT</span>
        </RouteLink>
        <nav aria-label="展示页导航">
          <a href="#purpose">项目目的</a>
          <a href="#process">使用流程</a>
          <a href="#demo">试用场景</a>
        </nav>
        <div className="showcase-nav__actions">
          <a className="showcase-nav__companion" download href={BROWSER_COMPANION_DOWNLOAD_URL}>下载浏览器伴侣</a>
          <RouteLink className="showcase-nav__cta" to="/login">进入资料库 <ArrowIcon /></RouteLink>
        </div>
      </header>

      <main id="showcase-main" tabIndex={-1}>
        <section className="showcase-hero" aria-labelledby="showcase-title">
          <div className="showcase-hero__grid" aria-hidden="true" />
          <div className="showcase-hero__copy">
            <p className="showcase-kicker"><span>你的私人视频资料库</span><span>2026 / HACKATHON</span></p>
            <h1 id="showcase-title">让收藏过的知识，<em>再次可用。</em></h1>
            <p className="showcase-hero__lead">
              散落在视频中的知识与信息，从此成为你的助手与知识库。
            </p>
            <div className="showcase-hero__actions">
              <a className="showcase-button showcase-button--signal" href="#demo">先试一个真实场景 <ArrowIcon /></a>
              <a className="showcase-button showcase-button--line" href="#process">查看工作方式</a>
            </div>
          </div>

          <div className="showcase-hero__instrument" aria-label="从视频到可追溯答案的处理路径">
            <div
              className="instrument-cover-stack"
              aria-label="资料库中的三个真实视频来源"
              onMouseLeave={() => setHeroCoverId(null)}
            >
              {demoScenes.map((scene, index) => (
                <a
                  className={`instrument-cover${heroCoverId === scene.id ? " is-front" : ""}`}
                  href="#demo"
                  aria-label={`打开 ${scene.sourceTitle} 问答场景`}
                  key={scene.id}
                  onMouseEnter={() => setHeroCoverId(scene.id)}
                  onFocus={() => setHeroCoverId(scene.id)}
                  onBlur={() => setHeroCoverId(null)}
                  onClick={() => selectDemo(scene.id)}
                >
                  <span className="instrument-cover__wire" aria-hidden="true" />
                  <img
                    src={scene.thumbnailUrl}
                    alt={`${scene.sourceTitle} 视频封面`}
                    width="480"
                    height="360"
                    decoding="async"
                    fetchPriority={index === demoScenes.length - 1 ? "high" : "auto"}
                  />
                  <span className="instrument-cover__caption">
                    <span>{scene.index}</span>
                    <strong>{scene.sourceTitle}</strong>
                  </span>
                </a>
              ))}
            </div>
            <div className="instrument-readout">
              <p><span>你收藏</span><strong>一段 YouTube 视频</strong></p>
              <p><span>系统整理</span><strong>字幕与重点内容</strong></p>
              <p><span>你获得</span><strong>答案 / 原文 / 时间点</strong></p>
            </div>
          </div>

          <div className="showcase-hero__rail" aria-label="项目核心能力">
            <span>01 / 收藏</span>
            <span>02 / 理解</span>
            <span>03 / 检索</span>
            <span>04 / 回源</span>
          </div>
        </section>

        <section className="showcase-section showcase-purpose" id="purpose" aria-labelledby="purpose-title">
          <div className="showcase-section__label">
            <span>01</span>
            <p>WHY IT EXISTS</p>
          </div>
          <div className="showcase-purpose__content">
            <p className="showcase-overline">项目目的</p>
              <h2 id="purpose-title">不要让遗忘成为<br /><em>收藏视频的终点。</em></h2>
            <div className="showcase-purpose__statement">
              <p>
                当我们按下收藏键的刹那，你是否会想到这是你最后一次与你的视频碰面？我们不希望视频只成为收藏夹的一串链接，我们希望当你有需要的时候，能一眼找到你想要的内容。Notebook Agent 不仅能帮你记住视频在哪里，更能提醒你视频讲了什么。
              </p>
              <p>
                它先从你的资料库里找到相关原文，再组织答案；找不到时会明确说明，不会凭模型记忆补出一个看似合理的结论。
              </p>
            </div>
          </div>
          <div className="showcase-purpose__proofs" aria-label="项目原则">
            <article>
              <span>01</span>
              <h3>独立资料空间</h3>
              <p>每位用户拥有独立的私人资料库。页面内容、检索结果与回答依据都限定在当前账户范围内，不与其他用户的数据混用。</p>
            </article>
            <article>
              <span>02</span>
              <h3>基于原文生成回答</h3>
              <p>系统先检索资料库中的相关字幕片段，再据此组织答案；缺少足够依据时会明确说明，不用模型记忆补全。</p>
            </article>
            <article>
              <span>03</span>
              <h3>答案依据全程可追溯</h3>
              <p>回答会同时关联视频标题、原文片段与对应时间点，方便随时返回原视频，核对结论所在的完整上下文。</p>
            </article>
          </div>
        </section>

        <section className="showcase-section showcase-audience" aria-labelledby="audience-title">
          <div className="showcase-section__label">
            <span>02</span>
            <p>WHO IT SERVES</p>
          </div>
          <div className="showcase-audience__heading">
            <p className="showcase-overline">适用人群</p>
            <h2 id="audience-title">
              <span className="showcase-audience__lead-in">为了让你跳过等待而设计：</span>
              <span>“我好像看过这个……<br />我找找？”</span>
            </h2>
          </div>
          <div className="showcase-audience__grid">
            {audiences.map((audience) => (
              <article key={audience.title}>
                <AudienceIcon name={audience.icon} />
                <h3>{audience.title}</h3>
                <p>{audience.copy}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="showcase-section showcase-process" id="process" aria-labelledby="process-title">
          <div className="showcase-section__label showcase-section__label--light">
            <span>03</span>
            <p>HOW IT WORKS</p>
          </div>
          <div className="showcase-process__heading">
            <p className="showcase-overline">视频知识处理流程</p>
            <h2 id="process-title">从视频归档，<br />到可追溯的回答。</h2>
            <p>视频链接提交后，系统会在后台提取标题、章节和字幕，建立可检索的内容索引；处理期间不影响继续聊天或浏览资料库。</p>
          </div>
          <ol className="showcase-process__list">
            {processSteps.map((step) => (
              <li key={step.index}>
                <span className="process-index">{step.index}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.copy}</p>
                </div>
                <code>{step.output}</code>
              </li>
            ))}
          </ol>
        </section>

        <section className="showcase-section showcase-demo" id="demo" aria-labelledby="demo-title">
          <div className="showcase-section__label">
            <span>04</span>
            <p>TRY THE FLOW</p>
          </div>
          <div className="showcase-demo__heading">
            <div>
              <p className="showcase-overline">真实来源 · 预设试用</p>
              <h2 id="demo-title">选一个场景，<br />看答案出处。</h2>
            </div>
            <p>
              下列答案是根据公开字幕预先整理的交互演示，不会调用模型或上传数据。真实使用时，来源会替换成你自己的资料库内容。
            </p>
          </div>

          <div className="demo-selector" aria-label="选择试用场景">
            {demoScenes.map((scene) => (
              <button
                className={scene.id === activeDemoId ? "is-active" : undefined}
                key={scene.id}
                type="button"
                aria-pressed={scene.id === activeDemoId}
                onClick={() => selectDemo(scene.id)}
              >
                <span>{scene.index}</span>
                <strong>{scene.audience}</strong>
                <small>{scene.title}</small>
              </button>
            ))}
          </div>

          <div className="demo-workbench">
            <aside className="demo-source-card">
              <div className="demo-source-card__visual">
                <img
                  src={activeDemo.thumbnailUrl}
                  alt={`当前场景：${activeDemo.sourceTitle} 视频封面`}
                  width="480"
                  height="360"
                  decoding="async"
                />
                <div className="demo-subtitle-language" role="group" aria-label="片段要点语言">
                  <button
                    type="button"
                    aria-label="显示中文要点"
                    aria-pressed={summaryLanguage === "zh"}
                    onClick={() => setSummaryLanguage("zh")}
                  ><span aria-hidden="true">中</span></button>
                  <button
                    type="button"
                    aria-label="显示英文要点"
                    aria-pressed={summaryLanguage === "en"}
                    onClick={() => setSummaryLanguage("en")}
                  ><span aria-hidden="true">EN</span></button>
                </div>
                <div className="demo-subtitle-bar" data-testid="demo-subtitle-ticker">
                  <span className="demo-subtitle-bar__mark" aria-hidden="true">要点</span>
                  <span className="sr-only">片段要点：{activeSummary}</span>
                  <div className="demo-subtitle-viewport" aria-hidden="true">
                    <div
                      className={`demo-subtitle-track demo-subtitle-track--${summaryDuration}`}
                      key={`${activeDemo.id}-${summaryLanguage}`}
                    >
                      <span>{activeSummary}</span>
                      <span>{activeSummary}</span>
                    </div>
                  </div>
                </div>
              </div>
              <div className="demo-source-card__body">
                <p className="showcase-overline">已导入来源</p>
                <h3>{activeDemo.sourceTitle}</h3>
                <p>{activeDemo.sourceCreator}</p>
                <div className="demo-source-card__status">
                  <span><i /> 字幕已整理</span>
                  <span>内容已可查找</span>
                </div>
                <a href={activeDemo.sourceUrl} target="_blank" rel="noreferrer">
                  查看原视频 <ArrowIcon />
                </a>
              </div>
            </aside>

            <div className="demo-console">
              <div className="demo-console__bar">
                <span>NOTEBOOK AGENT / 场景试用</span>
                <span className="demo-console__mode"><i /> 来源核对模式</span>
              </div>
              <div className="demo-question">
                <span>你的问题</span>
                <p>{activeDemo.question}</p>
              </div>
              <div className="demo-pipeline" aria-label="预设回答演示步骤">
                <span className={hasRun ? "is-complete" : undefined}>01 理解问题</span>
                <span className={hasRun ? "is-complete" : undefined}>02 查找相关片段</span>
                <span className={hasRun ? "is-complete" : undefined}>03 核对原文</span>
              </div>
              {!hasRun ? (
                <div className="demo-console__ready">
                  <button className="showcase-button showcase-button--signal" type="button" onClick={() => setHasRun(true)}>
                    查看这次回答 <ArrowIcon />
                  </button>
                </div>
              ) : (
                <article className="demo-answer" aria-live="polite">
                  <div className="demo-answer__label"><BrandLogo className="demo-answer__mark" /><p>基于 3 个字幕片段</p></div>
                  <p className="demo-answer__lead">
                    <TypewriterText text={activeDemo.answerLead} />
                  </p>
                  <ol>
                    {activeDemo.answerPoints.map((point) => (
                      <li key={point}>
                        <TypewriterText text={point} />
                      </li>
                    ))}
                  </ol>
                  <div className="demo-evidence">
                    <p>可核对证据</p>
                    {activeDemo.evidence.map((evidence) => (
                      <a href={evidence.url} key={evidence.url} target="_blank" rel="noreferrer">
                        <strong>{evidence.label}</strong>
                        <span>{evidence.note}</span>
                        <ArrowIcon />
                      </a>
                    ))}
                  </div>
                  <button className="demo-reset" type="button" onClick={() => setHasRun(false)}>收起答案</button>
                </article>
              )}
            </div>
          </div>
        </section>

        <section className="showcase-cta" aria-labelledby="showcase-cta-title">
          <p className="showcase-overline">YOUR KNOWLEDGE / YOUR EVIDENCE</p>
          <h2 id="showcase-cta-title">答案与原文，<br />一步之遥</h2>
          <RouteLink className="showcase-button showcase-button--dark" to="/login">进入私人资料库 <ArrowIcon /></RouteLink>
          <p className="showcase-cta__note">Web 当前负责登录、资料库管理和字幕阅读；带来源问答发生在已启用的聊天或 MCP 入口 · 可用入口以部署配置为准</p>
        </section>
      </main>

      <footer className="showcase-footer">
        <RouteLink className="showcase-brand showcase-brand--footer" to="/">
          <BrandLogo className="showcase-brand__mark" />
          <span>NOTEBOOK / AGENT</span>
        </RouteLink>
        <p>
          <span>Built for EAZO Global Hackathon</span>
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">
            粤ICP备2026101890号-1
          </a>
        </p>
        <a href="#showcase-main">回到顶部 ↑</a>
      </footer>
    </div>
  );
}
