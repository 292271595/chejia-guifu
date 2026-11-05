const cfg = require('../../config.js');
const { ask, submitFeedback, blocksToText } = require('../../utils/request.js');

let seq = 0;
const nextId = () => `m${++seq}_${Date.now()}`;

Page({
  data: {
    messages: [],
    input: '',
    canSend: false,
    sending: false,
    scrollTo: '',
    quickQuestions: cfg.quickQuestions,
    disclaimer: cfg.disclaimer,
    // 反馈原因。code 与后端 FEEDBACK_REASONS 对应，label 只是给人看。
    // 不设"其他"以外的必选项：让人必须选一个才能提交，多数人会直接放弃。
    fbReasons: [
      { code: 'irrelevant', label: '答非所问' },
      { code: 'citation', label: '法条引错' },
      { code: 'outdated', label: '信息过时' },
      { code: 'incomplete', label: '没答到关键' },
      { code: 'unclear', label: '看不懂' }
    ]
  },

  onLoad() {
    console.info('[mode]', cfg.useCloud ? 'callContainer（云托管）' : 'wx.request（本地调试）');
  },

  /* ---------- 滚动到底 ---------- */
  scrollBottom() {
    // 两个锚点交替，避免 scroll-into-view 值不变时不触发
    this.setData({ scrollTo: this.data.scrollTo === 'tailA' ? 'tailB' : 'tailA' });
  },

  /* ---------- 输入 ---------- */
  onInput(e) {
    const v = e.detail.value;
    this.setData({ input: v, canSend: !!v.trim() });
  },

  onQuick(e) {
    const q = e.currentTarget.dataset.q;
    this.setData({ input: q, canSend: true });
    this.onSend();
  },

  /* ---------- 组装多轮历史 ---------- */
  /**
   * 取最近 3 轮问答，交给后端做追问补全。
   *
   * 只带文本、不带 blocks/sources：后端拿它判断「上一句在讲什么」，
   * 不需要细节。答案截断 150 字——整段答案会让请求体积翻倍，
   * 且超出部分对判断话题没有帮助。
   *
   * 加载中/失败的消息跳过，否则会把占位内容当成真实回答发过去。
   */
  buildHistory() {
    const MAX_MSGS = 6;      // 3 轮
    const MAX_Q = 200;
    const MAX_A = 150;
    const out = [];
    const msgs = this.data.messages || [];

    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      if (!m || m.loading || m.error) continue;

      if (m.role === 'user') {
        const t = (m.text || '').trim();
        if (t) out.push({ role: 'user', content: t.slice(0, MAX_Q) });
        continue;
      }

      if (m.role === 'ai') {
        let t = (m.text || '').trim();
        if (!t && m.blocks && m.blocks.length) t = blocksToText(m.blocks);
        if (!t) continue;
        out.push({ role: 'assistant', content: t.slice(0, MAX_A) });
      }
    }
    return out.slice(-MAX_MSGS);
  },

  /* ---------- 发送 ---------- */
  onSend() {
    const q = (this.data.input || '').trim();
    if (!q) return;
    if (this.data.sending) return;

    // 必须在 concat 之前取历史：setData 之后 this.data.messages 已经含本轮问题，
    // 再取会把同一句发两遍，模型容易以为是重复追问。
    const history = this.buildHistory();

    const msgs = this.data.messages.concat([
      { id: nextId(), role: 'user', text: q },
      { id: nextId(), role: 'ai', loading: true, blocks: [], sources: [], showSources: false }
    ]);
    const aiIndex = msgs.length - 1;

    this.setData({
      messages: msgs,
      input: '',
      canSend: false,
      sending: true
    }, () => this.scrollBottom());

    ask(q, history)
      .then((payload) => this.fillAi(aiIndex, payload))
      .catch((err) => {
        this.setData({
          [`messages[${aiIndex}]`]: {
            id: msgs[aiIndex].id,
            role: 'ai',
            blocks: [],
            sources: [],
            showSources: false,
            error: (err && err.message) || '出了点问题，稍后再试。'
          }
        }, () => this.scrollBottom());
      })
      .then(() => {
        this.setData({ sending: false });
      });
  },

  /* ---------- 填充回答 ---------- */
  fillAi(index, payload) {
    const ok = payload && payload.success !== false;
    const blocks = (payload && payload.blocks) || [];
    const sources = (payload && payload.sources) || [];
    // 追问有歧义时后端会给出几个候选问法（例如「要多久」在「C1 能开什么车」
    // 「那 B2 呢」之后到底问什么）。渲染成按钮让用户点一下，
    // 比让用户自己重说一遍要快，也不会因为措辞不同又没听懂。
    const clarify = (payload && payload.clarify) || [];
    const node = ok
      ? {
          id: this.data.messages[index].id,
          role: 'ai',
          blocks,
          sources,
          showSources: false,
          degraded: !!(payload && payload.degraded),
          degradedType: (payload && payload.degraded) || '',
          clarify,
          factHit: (payload && payload.fact_hit) || '',
          resolvedQuestion: (payload && payload.resolved_question) || '',
          // 超纲拒答。也保留"回答不准"按钮——拒错了（明明是车驾问题被误判）
          // 恰恰是最该收集的一类样本，没这个入口就永远发现不了。
          refused: !!(payload && payload.refused),
          scopeDomain: (payload && payload.scope_domain) || '',
          // 反馈面板状态。放在消息节点里而不是页面全局：每条回答各报各的，
          // 用全局变量会让"点第二条却展开了第一条"。
          fbOpen: false,
          fbReason: '',
          fbNote: '',
          fbDone: false,
          fbSending: false
        }
      : {
          id: this.data.messages[index].id,
          role: 'ai',
          blocks,
          sources: [],
          showSources: false,
          clarify: [],
          fbOpen: false,
          fbReason: '',
          fbNote: '',
          fbDone: false,
          fbSending: false,
          error: (payload && payload.message) || '服务暂时没有响应，稍后再试。'
        };

    this.setData({ [`messages[${index}]`]: node }, () => this.scrollBottom());
  },

  /* ---------- 回答不准确上报 ---------- */
  /** 往上找这条 AI 回答对应的用户问题。中间可能夹着别的消息，所以循环找。 */
  findQuestion(index) {
    const msgs = this.data.messages || [];
    for (let i = index - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m && m.role === 'user' && m.text) return m.text;
    }
    return '';
  },

  openFeedback(e) {
    const i = e.currentTarget.dataset.index;
    const m = this.data.messages[i];
    if (!m || m.fbDone) return;      // 已反馈过就不再展开，避免重复提交
    this.setData({ [`messages[${i}].fbOpen`]: !m.fbOpen });
  },

  pickReason(e) {
    const ds = e.currentTarget.dataset;
    const cur = this.data.messages[ds.index].fbReason;
    // 再点一下取消选中，允许"不想说原因，只想说不对"
    this.setData({ [`messages[${ds.index}].fbReason`]: cur === ds.code ? '' : ds.code });
  },

  onFbNote(e) {
    const i = e.currentTarget.dataset.index;
    this.setData({ [`messages[${i}].fbNote`]: e.detail.value });
  },

  sendFeedback(e) {
    const i = e.currentTarget.dataset.index;
    const m = this.data.messages[i];
    if (!m || m.fbSending || m.fbDone) return;

    this.setData({ [`messages[${i}].fbSending`]: true });

    submitFeedback({
      question: this.findQuestion(i),
      // 用正文而不是原始 payload：payload 早被丢掉了，而用户看到的就是这段文字。
      // 他判断"答错了"，依据的是屏幕上这段话，报回来的也应该是这段话。
      answer: blocksToText(m.blocks),
      resolved_question: m.resolvedQuestion || '',
      fact_hit: m.factHit || '',
      degraded: m.degradedType || '',
      reason: m.fbReason || '',
      note: m.fbNote || ''
    }).then((res) => {
      const done = !!(res && res.success);
      this.setData({
        [`messages[${i}].fbSending`]: false,
        [`messages[${i}].fbDone`]: done,
        [`messages[${i}].fbOpen`]: false
      });
      wx.showToast({
        title: done ? '收到，谢谢' : '没传上去，再试一次',
        icon: done ? 'success' : 'none'
      });
    });
  },

  /* ---------- 来源展开 ---------- */
  toggleSources(e) {
    const i = e.currentTarget.dataset.index;
    const cur = this.data.messages[i].showSources;
    this.setData({ [`messages[${i}].showSources`]: !cur });
  },

  /* ---------- 复制 ---------- */
  copyAnswer(e) {
    const i = e.currentTarget.dataset.index;
    const m = this.data.messages[i];
    const text = blocksToText(m.blocks) + '\n\n' + cfg.disclaimer;
    wx.setClipboardData({ data: text });
  },

  goAbout() {
    wx.navigateTo({ url: '/pages/about/about' });
  },

  onShareAppMessage() {
    return { title: '车驾规问答 · 驾照年检记分查得清', path: '/pages/index/index' };
  }
});
