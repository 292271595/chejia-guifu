const cfg = require('./config.js');

App({
  onLaunch() {
    if (cfg.useCloud) {
      if (!wx.cloud) {
        console.error('当前基础库不支持 wx.cloud，请把调试基础库调到 2.23.0 以上');
      } else {
        wx.cloud.init({ env: cfg.cloudEnv, traceUser: true });
      }
    } else {
      console.info('[config] 未配置云托管环境，当前为本地调试模式 →', cfg.baseUrl);
    }
  },

  globalData: {
    cloudReady: cfg.useCloud
  }
});
