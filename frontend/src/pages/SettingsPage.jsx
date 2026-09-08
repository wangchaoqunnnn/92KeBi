import { useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Loading, ErrorBox } from '../components/ui'

/* 运行时配置页: 企业微信推送 webhook 与推送点击直达地址(存DB, 即时生效, 无需重启) */
export default function SettingsPage({ route, params, nav }) {
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [webhook, setWebhook] = useState('')
  const [pageUrl, setPageUrl] = useState('')
  const [msg, setMsg] = useState('')

  const load = () => {
    api.adminSettings()
      .then((d) => { setCfg(d); setPageUrl(d.page_url || ''); setWebhook('') })
      .catch((e) => setErr(String(e?.message || e)))
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      const d = await api.adminSettingsSave({
        wechat_webhook: webhook.trim(),
        page_url: pageUrl.trim(),
      })
      setMsg(`已保存 ✅ 当前跳转地址：${d.page_effective}｜webhook 数量：${d.hook_count}`)
      setWebhook('')
      load()
    } catch (e) {
      setMsg(`保存失败：${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }
  const testPush = async () => {
    setBusy(true); setMsg('')
    try {
      const d = await api.opsPushTest()
      setMsg(d.sent > 0
        ? `测试消息已送达微信 ✅（跳转地址将使用：${cfg ? cfg.page_effective : '…'}）`
        : `测试消息未送达：${(d.reason || '未知').slice(0, 180)}`)
    } catch (e) {
      setMsg(`推送测试失败：${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  if (err) return <div className="page"><ErrorBox error={err} onRetry={load} /></div>
  if (!cfg) return <div className="page"><Loading /></div>

  return (
    <div className="page">
      <style>{`
        .cfg-form{ display:flex; flex-direction:column; gap:14px; }
        .cfg-field label{ display:block; color:#8fa3c0; font-size:12.5px; margin-bottom:6px; }
        .cfg-field .hint{ color:#5f7598; font-size:11.5px; margin-top:4px; line-height:1.6; }
        .cfg-field input{ width:100%; padding:9px 12px; border-radius:9px; background:rgba(255,255,255,.05);
          border:1px solid rgba(255,255,255,.12); color:#e8eefc; font-size:13px; box-sizing:border-box; }
        .cfg-current{ background:rgba(122,169,255,.07); border:1px solid rgba(122,169,255,.22);
          border-radius:9px; padding:8px 12px; color:#cdd9f0; font-size:12px; }
        .cfg-current b{ color:#7aa9ff; }
        .cfg-actions{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
        .cfg-msg{ margin-top:10px; font-size:13px; line-height:1.7; color:#b9c6dd; white-space:pre-wrap; }
      `}</style>

      <div className="page-head">
        <div>
          <h2 className="page-title">推送与跳转配置</h2>
          <div className="page-sub">企业微信群机器人推送配置 —— 保存后立即生效，无需改环境变量/重启</div>
        </div>
      </div>

      <Card title="1、企业微信推送机器人 Webhook" extra={<span className="muted">推送「买入/卖出/删除等」事件到群</span>}>
        <div className="cfg-form">
          <div className="cfg-field">
            <label>机器人 Webhook URL（形如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx）</label>
            <input
              placeholder={cfg.wechat_webhook_masked ? `当前已配置：${cfg.wechat_webhook_masked}（输入新值可覆盖；留空并保存=清除）` : '输入企业微信群机器人 Webhook URL…'}
              value={webhook}
              onChange={(e) => setWebhook(e.target.value)}
            />
            <div className="hint">当前生效 hook 数：{cfg.hook_count}。留空保存即删除本配置（环境变量/文件里的仍生效）。</div>
          </div>
        </div>
      </Card>

      <Card title="2、点击推送消息后跳转的网页地址" extra={<span className="muted">推文尾部“点击打开”指向这里</span>}>
        <div className="cfg-form">
          <div className="cfg-field">
            <label>跳转地址（形如 https://wangchaoqun.top/92kebi/#/ops）</label>
            <input value={pageUrl} onChange={(e) => setPageUrl(e.target.value)} />
            <div className="hint">来源：{cfg.page_source === 'db' ? '当前为“配置页保存值”' : '当前使用默认/环境变量值'}；留空保存=回落到默认地址。</div>
          </div>
          <div className="cfg-current">当前实际生效：<b>{cfg.page_effective || '（未配置 → 纯文本推送不带链接）'}</b></div>
        </div>
      </Card>

      <Card title="3、保存 & 验证">
        <div className="cfg-actions">
          <button className="btn btn-sm" disabled={busy} onClick={save}>保存配置</button>
          <button className="btn btn-sm" disabled={busy} onClick={testPush}>发送测试推送</button>
          <span className="muted">“发送测试推送”会立即向群发一条测试消息（含可点击跳转链接）</span>
        </div>
        {msg && <div className="cfg-msg">{msg}</div>}
      </Card>
    </div>
  )
}
