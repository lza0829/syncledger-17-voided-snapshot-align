async function request(url, options = {}) {
  const r = await fetch(url, options)
  const text = await r.text()
  let body = null
  try { body = text ? JSON.parse(text) : null } catch { body = text }
  if (!r.ok) {
    const detail = body && body.detail
    const msg = typeof detail === 'string'
      ? detail
      : (detail && (detail.detail || detail.reason)) || text || `HTTP ${r.status}`
    const err = new Error(msg)
    err.status = r.status
    err.payload = typeof detail === 'object' ? detail : null
    err.body = body
    throw err
  }
  return body
}

export async function getJSON(url) {
  return request(url)
}

export async function postJSON(url, body = {}) {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function putJSON(url, body = {}) {
  return request(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
