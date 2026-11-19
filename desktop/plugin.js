// Coma: the pairing page in Hermes Desktop. Plain ESM, loaded uncompiled, so no JSX here.
import { Button, Input, PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host } from '@hermes/plugin-sdk'
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
  const [relay, setRelay] = useState('')
  const [editing, setEditing] = useState(false)

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

  const act = (path, body) => {
    if (busy || !rest) return
    setBusy(true)
    apply(rest(path, { method: 'POST', body })).finally(() => setBusy(false))
  }

  const state = status?.state ?? 'off'
  const on = state === 'on'
  const working = state === 'starting' || state === 'waiting'
  const restart = state === 'waiting' && status?.needs_restart
  const body = []

  if (on) {
    if (status.qr) {
      body.push(jsx(Qr, { rows: status.qr }, 'qr'))
      body.push(
        jsx(
          'div',
          { className: `text-sm ${muted}`, children: 'Open Coma on your phone, tap Scan pairing code and point it here.' },
          'hint'
        )
      )
    }
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
    body.push(
      jsx(
        'div',
        { className: `text-xs ${muted}`, children: 'A code pairs one phone and stops working after ten minutes.' },
        'warning'
      )
    )

    const limit = status.device_limit
    body.push(
      jsx(
        'div',
        {
          className: 'text-sm font-medium',
          children: limit
            ? `Phones paired: ${status.devices.length} of ${limit} (${status.plan} plan)`
            : `Phones paired: ${status.devices.length} (${status.plan} plan)`
        },
        'devices-title'
      )
    )
    status.devices.forEach(device => {
      body.push(
        jsxs(
          'div',
          {
            className: 'flex items-center justify-between gap-3 text-sm',
            children: [
              jsx('div', { className: 'truncate', children: device.name }),
              jsx(Button, {
                onClick: () => act('/devices/remove', { id: device.id }),
                disabled: busy,
                children: 'Unpair'
              })
            ]
          },
          `device-${device.id}`
        )
      )
    })
    if (limit && status.devices.length >= limit) {
      body.push(
        jsx(
          'div',
          {
            className: `text-xs ${muted}`,
            children: 'The free plan covers one phone. Plus, bought in the Coma app, covers any number.'
          },
          'limit'
        )
      )
    }
  } else if (restart) {
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
          children: state === 'waiting' ? 'Waiting for the Hermes API server to answer.' : 'Connecting to the relay.'
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
            'Pairing links this Hermes to a Coma relay, so your phone can reach it from anywhere. ' +
            'Your Hermes API key stays on this machine.'
        },
        'intro'
      )
    )
    if (state === 'error') body.push(jsx('div', { className: 'text-sm', children: status.error }, 'error'))
  }

  if (failure) body.push(jsx('div', { className: 'text-sm', children: failure }, 'failure'))

  if (on || working) {
    body.push(jsx(Button, { onClick: () => act('/stop'), disabled: busy, children: 'Disconnect' }, 'stop'))
  } else if (!restart) {
    body.push(jsx(Button, { onClick: () => act('/start'), disabled: busy, children: 'Start pairing' }, 'start'))
  }

  // The relay in use, and a way to point at your own.
  if (status?.relay) body.push(jsx(Row, { label: 'Relay', value: status.relay }, 'relay'))
  if (editing) {
    body.push(
      jsxs(
        'div',
        {
          className: 'flex flex-col gap-2',
          children: [
            jsx(Input, {
              value: relay,
              placeholder: 'https://relay.example.com',
              onChange: event => setRelay(event.target.value)
            }),
            jsx('div', {
              className: `text-xs ${muted}`,
              children: 'Phones paired through the current relay will have to pair again.'
            }),
            jsx(Button, {
              disabled: busy || !relay.trim(),
              onClick: () => {
                act('/relay', { address: relay.trim() })
                setEditing(false)
              },
              children: 'Use this relay'
            })
          ]
        },
        'relay-edit'
      )
    )
  } else {
    body.push(jsx(Button, { onClick: () => setEditing(true), children: 'Use my own relay' }, 'relay-change'))
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
