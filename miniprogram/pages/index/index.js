const cfg = require('../../config.js');
const { ask, blocksToText } = require('../../utils/request.js');

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
    disclaimer: cfg.disclaimer
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

  /* ---------- 发送 ---------- */
  onSend() {
    const q = (this.data.input || '').trim();
    if (!q) return;
    if (this.data.sending) return;

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

    ask(q)
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
    const node = ok
      ? {
          id: this.data.messages[index].id,
          role: 'ai',
          blocks,
          sources,
          showSources: false,
          degraded: !!(payload && payload.degraded),
          factHit: (payload && payload.fact_hit) || ''
        }
      : {
          id: this.data.messages[index].id,
          role: 'ai',
          blocks,
          sources: [],
          showSources: false,
          error: (payload && payload.message) || '服务暂时没有响应，稍后再试。'
        };

    this.setData({ [`messages[${index}]`]: node }, () => this.scrollBottom());
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
    return { title: '车驾规速查 · 驾照年检记分查得清', path: '/pages/index/index' };
  }
});
