const cfg = require('../config.js');

/**
 * 统一请求层
 * 对外暴露：ask(question, history) → Promise<payload>
 * payload: { success, blocks, fact_hit, sources, degraded?, resolved_question? }
 *
 * history 是最近几轮问答，用来让后端把「那 B2 呢」「要多久」这类追问补成完整问句。
 * 不传就是单轮，行为与改造前完全一致。
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
  // 后端原样返回的东西必须带上：404(detail)、405(detail)、500、网关 HTML
  // 在界面上全都长得一模一样，排查时只剩「服务没有响应」这一句，等于没有信息。
  try {
    err.detail = typeof data === 'string' ? data.slice(0, 300) : JSON.stringify(data).slice(0, 300);
  } catch (e) {
    err.detail = '(无法序列化)';
  }
  return err;
}

function _post(path, body) {
  return new Promise((resolve, reject) => {
    if (cfg.useCloud) {
      // 私有链路：header 里的 X-WX-SERVICE 必须与服务名完全一致，否则 404
      wx.cloud.callContainer({
        config: { env: cfg.cloudEnv },
        path: path,
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
      url: cfg.baseUrl + path,
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

function ask(question, history) {
  const body = { question: question, format: 'blocks' };
  // 第一轮不带历史：少传几百字节，也让后端直接走单轮快路径（不做改写）
  if (history && history.length) body.history = history;
  return _post('/api/query', body);
}

/**
 * 上报「这条回答不准确」。
 *
 * 带上整轮上下文，不只是"用户觉得不对"这一句——后端拿到原问题、
 * 系统当时理解成的问句、命中的处理器、是否降级，才能复现到底是哪一步错了。
 * 只报一个「不对」，我们回头还是只能靠猜。
 *
 * 这个请求永远不该阻塞用户：失败了也 resolve，页面把按钮标成"没传上去"就好，
 * 没必要在他已经不满意的时候再弹一个红色错误框。
 */

// callContainer 的请求体有上限（官方 100KB），超了网关直接拒，
// 前端只会看到一句「没传上去」，看不出是长度问题。
// 所以这里按 60KB 兜底：**先压缩再发**，绝不因为长就放弃上报。
// 事实层答案最长 1048 字（约 3KB），正常永远碰不到这条线；
// 它防的是检索层偶尔吐出的超长回答，以及用户自己粘贴的大段内容。
const FB_MAX_BYTES = 60000;

function utf8Len(str) {
  let n = 0;
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) { n += 4; i += 1; }
    else n += 3;
  }
  return n;
}

function trimToBytes(str, limit) {
  if (utf8Len(str) <= limit) return { text: str, cut: false };
  // 按最坏情况（中文 3 字节）折算字符数，再留 2% 给省略标记，保证折算后一定不超
  const n = Math.floor(limit * 0.98 / 3);
  const head = Math.floor(n * 0.7);
  const tail = Math.max(1, n - head);
  const omitted = str.length - head - tail;
  return {
    text: str.slice(0, head) + '\n……（此处省略 ' + omitted + ' 字）……\n' + str.slice(-tail),
    cut: true
  };
}

function submitFeedback(fb) {
  const body = Object.assign({}, fb);
  const trimmed = trimToBytes(body.answer || '', FB_MAX_BYTES);
  if (trimmed.cut) {
    // 不弹提示打扰用户：他要的是"报上去"，不是"你的答案太长了"。
    // 但日志里记一笔，排查时能对上后端的 truncated 标记。
    console.warn('[feedback] 答案超长，压缩后上报：原 ' + (body.answer || '').length + ' 字');
    body.answer = trimmed.text;
  }
  return _post('/api/feedback', body).catch((err) => {
    // 打成一行纯文本，别只丢一个 Error 对象——真机调试里对象是收起的，
    // 状态码这种关键字段永远看不到。
    console.warn(
      '[feedback] 提交失败 status=' + ((err && err.statusCode) === undefined ? 'undefined' : err.statusCode) +
      ' detail=' + ((err && err.detail) || '(空)') +
      ' msg=' + ((err && err.message) || '')
    );
    return { success: false, message: (err && err.message) || '没传上去' };
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

module.exports = { ask, submitFeedback, blocksToText };
