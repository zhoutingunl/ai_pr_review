/**
 * 用户行为埋点统一 SDK。
 *
 * 用法: track(event, payload)
 * 事件统一上报 POST /api/track，失败静默（埋点不影响业务）。
 */
function track(event, payload) {
  try {
    const body = JSON.stringify({event: event, payload: payload || {}});
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/track', new Blob([body], {type: 'application/json'}));
      return;
    }
    fetch('/api/track', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body,
      keepalive: true,
    }).catch(function () {});
  } catch (e) { /* 埋点失败静默 */ }
}
