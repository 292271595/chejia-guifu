const cfg = require('../../config.js');

Page({
  data: {
    appName: cfg.appName,
    version: cfg.version,
    feedbackEmail: cfg.feedbackEmail,
    laws: [
      '《机动车驾驶证申领和使用规定》（公安部令第 172 号）',
      '《道路交通安全违法行为记分管理办法》（公安部令第 163 号）',
      '《机动车登记规定》（公安部令第 164 号）',
      '《中华人民共和国道路交通安全法》及其实施条例',
      '《关于深化机动车检验制度改革优化车检服务工作的意见》（公交管〔2022〕295 号）',
      '《机动车安全技术检验项目和方法》（GB 38900—2020）'
    ]
  },

  copyMail() {
    wx.setClipboardData({ data: this.data.feedbackEmail });
  }
});
