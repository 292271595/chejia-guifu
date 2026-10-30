/**
 * 全局配置
 *
 *  部署前必改的两项：CLOUD_ENV、SERVICE_NAME。
 *  两项都填好 → 自动走「云托管私有链路 callContainer」（免域名免备案，正式环境用这个）。
 *  任一为空    → 自动退回 wx.request 直连 BASE_URL（仅本地开发者工具调试用，
 *               需在开发者工具「详情 - 本地设置」勾选“不校验合法域名”）。
 */
const CLOUD_ENV = 'prod-d9gclmh4i4080ad81';   // 云托管环境 ID（2026-10-30 创建）
const SERVICE_NAME = 'rag-backend';           // 云托管服务名，必须与控制台完全一致
const BASE_URL = 'http://127.0.0.1:8000';   // 仅本地调试生效

module.exports = {
  appName: '车驾规问答',
  version: '0.1.0',
  feedbackEmail: '',

  useCloud: !!(CLOUD_ENV && SERVICE_NAME),
  cloudEnv: CLOUD_ENV,
  serviceName: SERVICE_NAME,
  baseUrl: BASE_URL,

  // 免责与 AI 标识文案（集中在这里，改一处全局生效）
  disclaimer: '非官方应用，内容依据公开法规原文整理，仅供参考，具体以公安交管部门口径为准。',
  aiNotice: '本回答由 AI 依据法规原文整理生成',

  // 首页示例问题：都命中确定性事实层，0 秒返回，用来验证链路是否通
  quickQuestions: [
    '非营运小客车几年检一次',
    '超速 20% 记几分',
    'C1 能开什么车',
    '驾驶证过期 2 年怎么恢复',
    'A2 增驾 C6 要考什么',
    '70 岁以上还能开大车吗'
  ]
};
