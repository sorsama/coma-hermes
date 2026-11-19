// Coma: the pairing page in Hermes Desktop. Plain ESM, loaded uncompiled, so no JSX here.
import { Button, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PAGE_PATH = '/coma'

// Set in register(); the page reaches the plugin's own backend (/api/plugins/coma) through it.
let rest = null

const muted = 'text-(--ui-text-tertiary)'

/** A QR drawn from rows of '1' and '0'. A code must be dark on white to scan, whatever the theme. */
function Qr({ rows }) {
  const quiet = 3
  const size = rows.length + quiet * 2
  let path = ''
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === '1') path += `M${x + quiet} ${y + quiet}h1v1h-1z`
    }
  })
  return jsxs('svg', {
    viewBox: `0 0 ${size} ${size}`,
    width: 264,
    height: 264,
    shapeRendering: 'crispEdges',
    role: 'img',
    'aria-label': 'Pairing code for the Coma app',
    style: { borderRadius: 12 },
    children: [
      jsx('rect', { width: size, height: size, fill: '#ffffff' }),
      jsx('path', { d: path, fill: '#000000' })
    ]
  })
}

function Row({ label, value }) {
  return jsxs('div', {
    className: 'flex flex-col gap-0.5',
    children: [
      jsx('div', { className: `text-xs ${muted}`, children: label }),
      jsx('div', { className: 'font-mono text-sm break-all select-text', children: value })
    ]
  })
}

function explain(failure) {
  const text = failure instanceof Error ? failure.message : String(failure)
  return /\b404\b/.test(text)
    ? 'The agent half of this plugin is off. Run "hermes plugins enable coma" in a terminal, then restart Hermes.'
    : text
}

function ComaPage() {
  const [status, setStatus] = useState(null)
  const [failure, setFailure] = useState(null)
  const [busy, setBusy] = useState(false)

  const apply = promise =>
    promise.then(
      next => {
        setStatus(next)
        setFailure(null)
      },
      error => setFailure(explain(error))
    )

  useEffect(() => {
    let alive = true
    const load = () => {
      if (alive && rest) apply(rest('/status'))
    }
    load()
    const timer = setInterval(load, 3000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const act = path => {
    if (busy || !rest) return
    setBusy(true)
    apply(rest(path, { method: 'POST' })).finally(() => setBusy(false))
  }

  const state = status?.state ?? 'off'
  const on = state === 'on'
  const working = state === 'starting' || state === 'waiting'
  const body = []

  if (on) {
    if (status.qr) body.push(jsx(Qr, { rows: status.qr }, 'qr'))
    body.push(
      jsx(
        'div',
        {
          className: `text-sm ${muted}`,
          children: status.qr
            ? 'Open Coma on your phone, tap Scan pairing code and point it here.'
            : 'Enter this address and key in Coma: Connect, then Enter an address by hand.'
        },
        'hint'
      )
    )
    if (status.code) {
      body.push(
        jsx(
          Row,
          {
            label: `On the same Wi-Fi, Coma lists this machine as "${status.name}". Pairing code`,
            value: `${status.code.slice(0, 3)} ${status.code.slice(3)}`
          },
          'code'
        )
      )
    } else if (status.lan_error) {
      body.push(jsx('div', { className: `text-sm ${muted}`, children: status.lan_error }, 'lan'))
    }
    body.push(jsx(Row, { label: 'Address', value: status.address }, 'address'))
    body.push(
      jsx(
        'div',
        {
          className: `text-xs ${muted}`,
          children: 'Anyone with this code can run commands on this machine, so keep it off screen shares.'
        },
        'warning'
      )
    )
  } else if (state === 'waiting' && status.needs_restart) {
    body.push(
      jsx(
        'div',
        {
          className: 'text-sm',
          children: 'Coma turned on the Hermes API server. Restart Hermes so it starts, then start pairing again.'
        },
        'restart-text'
      )
    )
    body.push(jsx(Button, { onClick: () => host.restartGateway(), children: 'Restart Hermes' }, 'restart'))
  } else if (working) {
    body.push(
      jsx(
        'div',
        {
          className: `text-sm ${muted}`,
          children: state === 'waiting' ? 'Waiting for the Hermes API server to answer.' : 'Opening a Cloudflare tunnel.'
        },
        'working'
      )
    )
  } else {
    body.push(
      jsx(
        'div',
        {
          className: `text-sm ${muted}`,
          children:
            'Pairing opens a free Cloudflare tunnel to this Hermes and shows a code for the Coma app. ' +
            'The tunnel stays open until you stop it or Hermes quits.'
        },
        'intro'
      )
    )
    if (state === 'error') body.push(jsx('div', { className: 'text-sm', children: status.error }, 'error'))
  }

  if (failure) body.push(jsx('div', { className: 'text-sm', children: failure }, 'failure'))

  if (on || working) {
    body.push(jsx(Button, { onClick: () => act('/stop'), disabled: busy, children: 'Stop pairing' }, 'stop'))
  } else if (!(state === 'waiting' && status?.needs_restart)) {
    body.push(jsx(Button, { onClick: () => act('/start'), disabled: busy, children: 'Start pairing' }, 'start'))
  }

  return jsx('div', {
    className: 'h-full overflow-auto',
    children: jsxs('div', {
      className: 'mx-auto flex max-w-md flex-col gap-4 p-6',
      children: [
        jsxs(
          'div',
          {
            className: 'flex flex-col gap-1',
            children: [
              jsx('div', { className: 'text-lg font-medium', children: 'Coma' }),
              jsx('div', { className: `text-sm ${muted}`, children: 'Pair the Coma phone app with this Hermes.' })
            ]
          },
          'title'
        ),
        ...body
      ]
    })
  })
}

export default {
  id: 'coma', // must match the folder name
  name: 'Coma',
  description: 'Shows the QR and code that pair the Coma phone app with this Hermes.',
  register(ctx) {
    rest = (path, opts) => ctx.rest(path, opts)
    ctx.onDispose(() => {
      rest = null
    })
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PAGE_PATH }, render: () => jsx(ComaPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 60, data: { path: PAGE_PATH, label: 'Coma', codicon: 'device-mobile' } },
      {
        id: 'palette',
        area: PALETTE_AREA,
        data: { id: 'coma.pair', keywords: ['coma', 'pair', 'phone', 'qr'], label: 'Pair the Coma app', run: () => host.navigate(PAGE_PATH) }
      }
    ])
  }
}
