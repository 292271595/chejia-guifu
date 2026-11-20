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

/* ---------- 反馈失败留痕与补传 ----------
 *
 * 为什么要有这一层：反馈是用户主动点的那一下，一次失败就永久消失。
 * 网络抖一下、发版期间 404 一下，这条样本就没了——而它恰恰是最贵的样本
 * （用户愿意花时间告诉你哪里错了）。
 *
 * 失败分两种，处理方式不同：
 *   · 后端收了但明确说不行（限流、服务端异常）—— 再发还是同样结果，不排队，只留痕；
 *   · 根本没到后端（断网/超时/网关 5xx/接口不存在）—— 存进队列，下次进页面补传。
 *
 * 队列最多 10 条、每条最多补 3 次，防止接口一直 404 时无限堆积。
 * 留痕放在 Storage 里，真机上开发者工具 → Storage 面板就能看到，
 * 不用连真机调试也能知道「到底失败在哪一步」。
 */
const FB_QUEUE_KEY = 'fb_pending';
const FB_ERR_KEY = 'fb_errors';
const FB_QUEUE_MAX = 10;
const FB_RETRY_MAX = 3;

function _readArr(key) {
  try {
    const v = wx.getStorageSync(key);
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
}

function _writeArr(key, arr, max) {
  try {
    wx.setStorageSync(key, arr.slice(-max));
  } catch (e) {
    console.warn('[feedback] 本地存储写入失败', e);
  }
}

function _noteFail(info) {
  const arr = _readArr(FB_ERR_KEY);
  arr.push(info);
  _writeArr(FB_ERR_KEY, arr, 20);
}

function _enqueue(body, why) {
  const arr = _readArr(FB_QUEUE_KEY);
  arr.push({ body: body, tries: 0, why: why || '', at: Date.now() });
  _writeArr(FB_QUEUE_KEY, arr, FB_QUEUE_MAX);
}

function _giveUpOrKeep(arr, item, why) {
  item.tries = (item.tries || 0) + 1;
  if (item.tries >= FB_RETRY_MAX) {
    arr.shift();
    _noteFail({ at: Date.now(), kind: 'dropped', msg: why });
    console.warn('[feedback] 补传 ' + FB_RETRY_MAX + ' 次仍失败，丢弃：' + why);
  } else {
    arr[0] = item;
  }
  _writeArr(FB_QUEUE_KEY, arr, FB_QUEUE_MAX);
  return 0;
}

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
  return _post('/api/feedback', body).then((res) => {
    if (res && res.success) return res;
    // 走到了后端、被后端明确拒收（限流 / 服务端异常）。再发也是同样结果，
    // 不排队；但要留痕，否则「被拒了多少次、什么原因」永远查不到。
    _noteFail({ at: Date.now(), kind: 'rejected', msg: (res && res.message) || '' });
    return (res && res.success === false) ? res : { success: false, message: '没传上去' };
  }).catch((err) => {
    // 打成一行纯文本，别只丢一个 Error 对象——真机调试里对象是收起的，
    // 状态码这种关键字段永远看不到。
    const status = (err && err.statusCode) === undefined ? 'undefined' : err.statusCode;
    const detail = (err && err.detail) || '(空)';
    console.warn('[feedback] 提交失败 status=' + status + ' detail=' + detail +
      ' msg=' + ((err && err.message) || ''));
    _noteFail({ at: Date.now(), kind: 'error', status: status, detail: String(detail).slice(0, 200) });
    // 没到后端（断网/超时/网关 5xx/接口不存在）：存起来，下次进页面自动补传。
    _enqueue(body, 'status=' + status);
    return {
      success: false,
      queued: true,
      message: (err && err.message) || '没传上去'
    };
  });
}

/**
 * 把上次没传上去的反馈补发一条。返回补发成功的条数（0 或 1）。
 *
 * 一次只补一条：接口真挂了的时候，连发十条只会把启动拖慢、日志刷满。
 * 每次进首页补一条，攒的几条也就补完了。
 */
function flushFeedbackQueue() {
  const arr = _readArr(FB_QUEUE_KEY);
  if (!arr.length) return Promise.resolve(0);
  const item = arr[0];
  if (!item || !item.body) {
    arr.shift();
    _writeArr(FB_QUEUE_KEY, arr, FB_QUEUE_MAX);
    return Promise.resolve(0);
  }
  return _post('/api/feedback', item.body).then((res) => {
    if (res && res.success) {
      arr.shift();
      _writeArr(FB_QUEUE_KEY, arr, FB_QUEUE_MAX);
      console.info('[feedback] 补传成功，队列剩 ' + arr.length + ' 条');
      return 1;
    }
    return _giveUpOrKeep(arr, item, 'rejected:' + ((res && res.message) || ''));
  }).catch((err) => {
    return _giveUpOrKeep(arr, item, 'error:' + ((err && err.statusCode) || ''));
  });
}

/** 本地留存的失败记录，供排查用（真机上看不到 console，这个能看）。 */
function feedbackDiagnostics() {
  return {
    pending: _readArr(FB_QUEUE_KEY),
    errors: _readArr(FB_ERR_KEY)
  };
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

module.exports = { ask, submitFeedback, flushFeedbackQueue, feedbackDiagnostics, blocksToText };
