const cfg = require('../config.js');

/**
 * 统一请求层
 * 对外只暴露一个函数：ask(question) → Promise<payload>
 * payload: { success, blocks, fact_hit, sources, degraded? }
 *
 * 以后若从云托管切回「备案域名 + wx.request」，只改这个文件，页面无感。
 */
function normalizeError(res) {
  const data = (res && res.data) || {};
  let message = data.message || '';
  if (!message) {
    if (res && res.statusCode === 403) message = '当前为内测状态，暂未对你开放。';
    else if (res && res.statusCode === 400) message = '这个问题没能正常提交，换个问法再试一次。';
    else message = '服务暂时没有响应，请稍后再试。';
  }
  const err = new Error(message);
  err.statusCode = res && res.statusCode;
  return err;
}

function ask(question) {
  const body = { question: question, format: 'blocks' };

  return new Promise((resolve, reject) => {
    if (cfg.useCloud) {
      // 私有链路：header 里的 X-WX-SERVICE 必须与服务名完全一致，否则 404
      wx.cloud.callContainer({
        config: { env: cfg.cloudEnv },
        path: '/api/query',
        method: 'POST',
        header: {
          'X-WX-SERVICE': cfg.serviceName,
          'content-type': 'application/json'
        },
        data: body,
        success(res) {
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data);
          else reject(normalizeError(res));
        },
        fail(e) {
          reject(normalizeError({ statusCode: 0, data: { message: '网络不太稳定，检查一下网络再试。' } }));
          console.warn('[callContainer] fail', e);
        }
      });
      return;
    }

    // 本地调试分支
    wx.request({
      url: cfg.baseUrl + '/api/query',
      method: 'POST',
      header: { 'content-type': 'application/json' },
      data: body,
      timeout: 20000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data);
        else reject(normalizeError(res));
      },
      fail() {
        reject(normalizeError({
          statusCode: 0,
          data: { message: '连不上本地后端。确认 8000 端口在跑，并在开发者工具里勾选“不校验合法域名”。' }
        }));
      }
    });
  });
}

/** blocks 还原成纯文本，用于“复制答案” */
function blocksToText(blocks) {
  const out = [];
  (blocks || []).forEach((b) => {
    if (b.type === 'h') out.push('\n' + (b.text || '') + '\n');
    else if (b.type === 'p' || b.type === 'quote') out.push((b.text || '') + '\n');
    else if (b.type === 'list') out.push((b.items || []).map((x) => '· ' + x).join('\n') + '\n');
    else if (b.type === 'table') {
      const header = (b.header || []).join(' | ');
      const rows = (b.rows || []).map((r) => r.join(' | ')).join('\n');
      out.push([header, rows].filter(Boolean).join('\n') + '\n');
    }
  });
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

module.exports = { ask, blocksToText };
