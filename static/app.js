// SmartPlug Hub frontend — theme, device card rendering.

const API_BASE = '/api/v1'

const TOAST_ICONS = {
    success: 'bi-check-circle-fill',
    warning: 'bi-exclamation-triangle-fill',
    danger: 'bi-x-circle-fill',
    info: 'bi-info-circle-fill',
}

const currentDevices = {}
let allDeviceIds = []
let activeGroup = 'all'
let searchQuery = ''

const notificationHistory = []
let unreadCount = 0

const pendingToggles = new Set()


async function fetchDevices() {
    const response = await fetch(`${API_BASE}/devices`)
    if (!response.ok) throw new Error('Failed to fetch devices')
    return response.json()
}

async function controlDevice(deviceId, action, childId = null) {
    const body = { is_on: action === 'on' }
    if (childId !== null) body.child_id = childId

    const response = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })

    if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail?.message || 'Control failed')
    }
    return response.json()
}

async function refreshDevice(deviceId) {
    const response = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/refresh`, {
        method: 'POST'
    })
    if (!response.ok && response.status !== 503) {
        const error = await response.json()
        throw new Error(error.detail?.message || 'Refresh failed')
    }
    return response.json()
}

function showToast(message, type = 'danger') {
    const container = document.getElementById('toast-container')
    const colorClass = {
        success: 'text-bg-success',
        warning: 'text-bg-warning',
        danger: 'text-bg-danger',
        info: 'text-bg-info',
    }[type] || 'text-bg-danger'

    const icon = TOAST_ICONS[type] || 'bi-circle-fill'

    const toastEl = document.createElement('div')
    toastEl.className = `toast ${colorClass}`
    toastEl.setAttribute('role', 'alert')
    toastEl.innerHTML = `
        <div class="d-flex align-items-center">
            <span class="toast-icon"><i class="bi ${icon}"></i></span>
            <div class="toast-body">${escapeHtml(message)}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto"
                    data-bs-dismiss="toast"></button>
        </div>
    `

    container.appendChild(toastEl)
    const toast = new bootstrap.Toast(toastEl, { delay: 5000 })
    toast.show()
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove())

    notificationHistory.unshift({ message, type, icon, time: new Date().toISOString() })
    if (notificationHistory.length > 20) notificationHistory.pop()
    unreadCount++
    updateNotifBadge()
}

function updateNotifBadge() {
    const badge = document.getElementById('notif-badge')
    if (!badge) return
    if (unreadCount > 0) {
        badge.textContent = unreadCount > 9 ? '9+' : String(unreadCount)
        badge.classList.remove('d-none')
    } else {
        badge.classList.add('d-none')
    }
}

function renderNotifDropdown() {
    const dropdown = document.getElementById('notif-dropdown')
    if (!dropdown) return
    if (notificationHistory.length === 0) {
        dropdown.innerHTML = '<p class="notif-empty">No notifications yet</p>'
        return
    }
    dropdown.innerHTML = notificationHistory.map(n => `
        <div class="notif-item">
            <span class="notif-item-icon type-${n.type}"><i class="bi ${n.icon}"></i></span>
            <div class="notif-item-body">
                <div class="notif-item-msg">${escapeHtml(n.message)}</div>
                <div class="notif-item-time">${formatTime(n.time)}</div>
            </div>
        </div>
    `).join('')
}

function toggleNotifDropdown() {
    const dropdown = document.getElementById('notif-dropdown')
    if (!dropdown) return
    const isOpen = !dropdown.classList.contains('d-none')
    if (isOpen) {
        dropdown.classList.add('d-none')
    } else {
        unreadCount = 0
        updateNotifBadge()
        renderNotifDropdown()
        dropdown.classList.remove('d-none')
    }
}

function escapeHtml(text) {
    if (!text) return ''
    const div = document.createElement('div')
    div.textContent = text
    return div.innerHTML
}

function formatTime(isoString) {
    try {
        const date = new Date(isoString)
        const offsetMin = -date.getTimezoneOffset()
        const sign = offsetMin >= 0 ? '+' : '-'
        const absMin = Math.abs(offsetMin)
        const hours = Math.floor(absMin / 60)
        const mins = absMin % 60
        const offset = mins ? `${hours}:${String(mins).padStart(2, '0')}` : `${hours}`
        const time = date.toLocaleTimeString(undefined, { hour12: false })
        return `${time} UTC${sign}${offset}`
    } catch {
        return ''
    }
}

function getAllDevices() {
    return allDeviceIds.map(id => currentDevices[id]).filter(Boolean)
}

function getFilteredDevices(devices) {
    if (searchQuery) {
        const q = searchQuery.toLowerCase()
        return devices.filter(d =>
            d.name.toLowerCase().includes(q) ||
            (d.children?.some(c => c.alias.toLowerCase().includes(q)))
        )
    }
    if (activeGroup !== 'all') {
        return devices.filter(d => d.group === activeGroup)
    }
    return devices
}

function renderTabs(devices) {
    const container = document.getElementById('tabs-nav')
    if (!container) return

    const groups = [...new Set(devices.filter(d => d.group).map(d => d.group))]

    // When searching, highlight All tab
    const effectiveActive = searchQuery ? 'all' : activeGroup

    const tabs = [
        { id: 'all', label: 'All', count: devices.length },
        ...groups.map(g => ({
            id: g,
            label: g,
            count: devices.filter(d => d.group === g).length,
        }))
    ]

    container.innerHTML = `
        <ul class="nav nav-tabs mb-3">
            ${tabs.map(tab => `
                <li class="nav-item">
                    <button class="nav-link ${effectiveActive === tab.id ? 'active' : ''}"
                            data-group="${escapeHtml(tab.id)}">
                        ${escapeHtml(tab.label)}
                        <span class="badge text-bg-secondary ms-1">${tab.count}</span>
                    </button>
                </li>
            `).join('')}
        </ul>
    `

    container.querySelectorAll('.nav-link').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.group))
    })
}

function switchTab(group) {
    activeGroup = group
    searchQuery = ''
    const searchInput = document.getElementById('search-input')
    if (searchInput) searchInput.value = ''
    const all = getAllDevices()
    renderTabs(all)
    renderDeviceGrid(getFilteredDevices(all))
}

function renderDevices(devices) {
    allDeviceIds = devices.map(d => d.id)
    for (const device of devices) {
        currentDevices[device.id] = device
    }
    renderTabs(devices)
    renderDeviceGrid(getFilteredDevices(devices))
}

function renderDeviceGrid(devices) {
    const container = document.getElementById('devices-container')

    if (getAllDevices().length === 0) {
        container.innerHTML = `
            <div class="alert alert-info">
                <strong>No devices</strong><br>
                Please configure whitelisted devices in <code>config/devices.json</code>.
            </div>
        `
        return
    }

    if (devices.length === 0) {
        container.innerHTML = `<div class="text-muted py-3">No matching devices.</div>`
        return
    }

    container.innerHTML = `
        <div class="row g-3">
            ${devices.map(device => `
                <div class="col-lg-4 col-md-6">
                    ${renderDeviceCard(device)}
                </div>
            `).join('')}
        </div>
    `
}

function renderDeviceCard(device) {
    const online = device.status === 'online'
    const stateClass = online ? 'online' : 'offline'

    let bodyHtml = ''
    if (device.is_strip && device.children && device.children.length > 0) {
        bodyHtml = device.children.map(child =>
            renderChildOutlet(device.id, child, online)
        ).join('')
    } else if (online) {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, device.is_on, true)}
            </div>
        `
    } else {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, false, false)}
            </div>
        `
    }

    const refreshBtn = !online ? `
        <button class="btn btn-sm refresh-device-btn"
                data-device-id="${escapeHtml(device.id)}"
                data-refresh
                title="Refresh">
            &#x21bb;
        </button>
    ` : ''

    const updatedTime = device.last_updated ? formatTime(device.last_updated) : ''

    return `
        <div class="card device-card h-100 state-${stateClass}" data-id="${device.id}">
            <div class="card-header d-flex justify-content-between align-items-center">
                <div>
                    <strong>${escapeHtml(device.name)}</strong>
                    ${device.model ? `<div class="device-model">${escapeHtml(device.type[0].toUpperCase() + device.type.slice(1))} ${escapeHtml(device.model)}</div>` : ''}
                    ${updatedTime ? `<div class="last-updated">Last updated: ${updatedTime}</div>` : ''}
                </div>
                ${refreshBtn}
            </div>
            <div class="card-body">
                ${bodyHtml}
            </div>
        </div>
    `
}

function renderChildOutlet(deviceId, child, online) {
    const onClass = child.is_on ? 'is-on' : ''

    return `
        <div class="child-outlet ${onClass}">
            <span class="outlet-name">${escapeHtml(child.alias)}</span>
            <div class="outlet-controls">
                ${renderToggleSwitch(deviceId, child.id, child.is_on, online)}
            </div>
        </div>
    `
}

function renderToggleSwitch(deviceId, childId, isOn, enabled) {
    const action = isOn ? 'off' : 'on'
    const onClass = isOn ? 'is-on' : ''
    const disabledAttr = enabled ? '' : 'disabled'
    const childAttr = childId !== null ? `data-child-id="${escapeHtml(childId)}"` : ''

    return `
        <button class="toggle-switch ${onClass}"
                data-device-id="${escapeHtml(deviceId)}"
                data-action="${action}"
                ${childAttr}
                title="${isOn ? 'Turn off' : 'Turn on'}"
                ${disabledAttr}>
        </button>
    `
}

function getTargetLabel(device, childId) {
    const name = device.name || device.id
    if (!childId || !device.children) return name
    const child = device.children.find(c => c.id === childId)
    return child ? `${name} / ${child.alias}` : name
}

async function loadDevices() {
    try {
        const data = await fetchDevices()
        renderDevices(data.devices)
    } catch (error) {
        console.error('Load devices error:', error)
        showToast('Failed to load devices: ' + error.message)
    }
}

async function handleToggle(deviceId, action, childId) {
    pendingToggles.add(deviceId)
    const card = document.querySelector(`[data-id="${deviceId}"]`)
    if (card) card.classList.add('loading')

    try {
        const result = await controlDevice(deviceId, action, childId)
        currentDevices[deviceId] = result
        updateCardFromState(deviceId, result)
        showToast(`Turned ${action}: ${getTargetLabel(result, childId)}`, 'success')
    } catch (error) {
        console.error('Toggle error:', error)
        const prev = currentDevices[deviceId]
        showToast(`${error.message}: ${prev ? getTargetLabel(prev, childId) : deviceId}`)
    } finally {
        pendingToggles.delete(deviceId)
        if (card) card.classList.remove('loading')
    }
}

async function handleRefresh(deviceId) {
    const card = document.querySelector(`[data-id="${deviceId}"]`)
    if (card) card.classList.add('loading')

    try {
        const result = await refreshDevice(deviceId)
        currentDevices[deviceId] = result

        const deviceName = result.name || deviceId
        if (result.status === 'online') {
            showToast(`Reconnected: ${deviceName}`, 'success')
        } else {
            showToast(`Still offline: ${deviceName}`, 'warning')
        }

        updateCardFromState(deviceId, result)
    } catch (error) {
        console.error('Refresh error:', error)
        showToast('Refresh failed: ' + error.message)
    } finally {
        if (card) card.classList.remove('loading')
    }
}

function updateCardFromState(deviceId, device) {
    const card = document.querySelector(`[data-id="${deviceId}"]`)
    if (!card) return

    const online = device.status === 'online'
    const wasOnline = card.classList.contains('state-online')
    if (online !== wasOnline) {
        loadDevices()
        return
    }

    if (!online) return

    if (device.children && device.children.length > 0) {
        for (const child of device.children) {
            const btn = card.querySelector(`.toggle-switch[data-child-id="${child.id}"]`)
            if (btn) {
                updateToggleButton(btn, child.id, child.is_on)
                btn.closest('.child-outlet')?.classList.toggle('is-on', child.is_on)
            }
        }
    } else if (device.is_on !== undefined && device.is_on !== null) {
        const btn = card.querySelector('.single-device-control .toggle-switch')
        if (btn) updateToggleButton(btn, null, device.is_on)
    }
}

function updateToggleButton(btn, childId, isOn) {
    btn.classList.toggle('is-on', isOn)
    btn.dataset.action = isOn ? 'off' : 'on'
    btn.setAttribute('title', isOn ? 'Turn off' : 'Turn on')
}

function setServerOffline(offline) {
    const banner = document.getElementById('server-offline-banner')
    if (banner) banner.classList.toggle('d-none', !offline)
}

function detectStatusChanges(newDevices) {
    for (const device of newDevices) {
        const prev = currentDevices[device.id]
        if (!prev) continue

        if (prev.status !== device.status) {
            if (device.status === 'online') {
                showToast(`Back online: ${device.name}`, 'success')
            } else {
                showToast(`Went offline: ${device.name}`, 'warning')
            }
            continue
        }

        if (device.status !== 'online') continue
        if (pendingToggles.has(device.id)) continue

        if (device.children?.length) {
            for (const child of device.children) {
                const prevChild = prev.children?.find(c => c.id === child.id)
                if (prevChild && prevChild.is_on !== child.is_on) {
                    const action = child.is_on ? 'on' : 'off'
                    showToast(`Turned ${action}: ${device.name} / ${child.alias}`, 'info')
                }
            }
        } else if (prev.is_on !== undefined && prev.is_on !== device.is_on) {
            const action = device.is_on ? 'on' : 'off'
            showToast(`Turned ${action}: ${device.name}`, 'info')
        }
    }
}

function connectSSE() {
    const es = new EventSource(`${API_BASE}/events`)

    es.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data)
            setServerOffline(false)
            detectStatusChanges(data.devices)
            renderDevices(data.devices)
        } catch (e) {
            console.error('SSE parse error:', e)
        }
    }

    es.onerror = () => setServerOffline(true)
}

document.addEventListener('DOMContentLoaded', () => {
    loadDevices()
    connectSSE()

    document.getElementById('devices-container').addEventListener('click', e => {
        const toggleBtn = e.target.closest('.toggle-switch:not([disabled])')
        if (toggleBtn) {
            handleToggle(toggleBtn.dataset.deviceId, toggleBtn.dataset.action, toggleBtn.dataset.childId ?? null)
            return
        }
        const refreshBtn = e.target.closest('[data-refresh]')
        if (refreshBtn) handleRefresh(refreshBtn.dataset.deviceId)
    })

    const searchInput = document.getElementById('search-input')
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            searchQuery = searchInput.value.trim()
            const all = getAllDevices()
            renderTabs(all)
            renderDeviceGrid(getFilteredDevices(all))
        })
    }

    const notifBtn = document.getElementById('notif-btn')
    if (notifBtn) {
        notifBtn.addEventListener('click', e => {
            e.stopPropagation()
            toggleNotifDropdown()
        })
    }

    document.addEventListener('click', e => {
        const wrapper = document.getElementById('notif-wrapper')
        if (wrapper && !wrapper.contains(e.target)) {
            const dropdown = document.getElementById('notif-dropdown')
            if (dropdown) dropdown.classList.add('d-none')
        }
    })
})
