/**
 * Kasa Web Controller - Frontend Application (v1 API)
 *
 * - Dark mode (default) with localStorage persistence
 * - Toast notifications (Bootstrap 5)
 * - Grid layout: 3 columns (lg), 2 columns (md), 1 column (sm)
 * - Online devices: toggle enabled, PATCH blocks until complete
 * - Offline devices: full topology shown, toggle greyed out disabled, Refresh button
 * - Polling: GET /api/v1/devices every 5s (zero I/O on server)
 */

const API_BASE = '/api/v1';
const POLL_INTERVAL = 5000;

let pollTimer = null;
let currentDevices = {};  // device_id -> device state

// === Theme ===
function initTheme() {
    const saved = localStorage.getItem('theme');
    const system = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const theme = saved || system;
    document.documentElement.setAttribute('data-bs-theme', theme);
    updateThemeIcon(theme);

    // Listen for system theme changes (only if user hasn't manually overridden)
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (!localStorage.getItem('theme')) {
            const next = e.matches ? 'dark' : 'light';
            document.documentElement.setAttribute('data-bs-theme', next);
            updateThemeIcon(next);
        }
    });
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-bs-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    updateThemeIcon(next);
}

function updateThemeIcon(theme) {
    const icon = document.getElementById('theme-icon');
    if (icon) icon.textContent = theme === 'dark' ? '\u2600' : '\u263E';
}

// === API Functions ===
async function fetchDevices() {
    const response = await fetch(`${API_BASE}/devices`);
    if (!response.ok) throw new Error('Failed to fetch devices');
    return response.json();
}

async function controlDevice(deviceId, action, childId = null) {
    const body = { action };
    if (childId !== null) body.child_id = childId;

    const response = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (!response.ok) {
        const error = await response.json();
        if (error.detail && typeof error.detail === 'object') {
            throw new Error(error.detail.message || 'Control failed');
        }
        throw new Error(error.detail || 'Control failed');
    }
    return response.json();
}

async function refreshDevice(deviceId) {
    const response = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/refresh`, {
        method: 'POST'
    });
    const data = await response.json();
    return data;
}

// === Toast Notifications ===
function showToast(message, type = 'danger') {
    const container = document.getElementById('toast-container');
    const colorClass = {
        success: 'text-bg-success',
        warning: 'text-bg-warning',
        danger: 'text-bg-danger',
        info: 'text-bg-info',
    }[type] || 'text-bg-danger';

    const toastEl = document.createElement('div');
    toastEl.className = `toast ${colorClass}`;
    toastEl.setAttribute('role', 'alert');
    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">${escapeHtml(message)}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto"
                    data-bs-dismiss="toast"></button>
        </div>
    `;

    container.appendChild(toastEl);
    const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
    toast.show();
    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

// === UI Helpers ===
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTime(isoString) {
    try {
        return new Date(isoString).toLocaleTimeString();
    } catch {
        return '';
    }
}

// === Rendering ===
function renderDevices(devices) {
    const container = document.getElementById('devices-container');

    if (devices.length === 0) {
        container.innerHTML = `
            <div class="alert alert-info">
                <strong>No devices</strong><br>
                Please configure whitelisted devices in <code>config/devices.json</code>.
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <div class="row g-3">
            ${devices.map(device => `
                <div class="col-md-6">
                    ${renderDeviceCard(device)}
                </div>
            `).join('')}
        </div>
    `;

    for (const device of devices) {
        currentDevices[device.id] = device;
    }
}

function renderDeviceCard(device) {
    const online = device.status === 'online';
    const stateClass = online ? 'online' : 'offline';

    let bodyHtml = '';
    if (device.is_strip && device.children && device.children.length > 0) {
        bodyHtml = device.children.map(child =>
            renderChildOutlet(device.id, child, online)
        ).join('');
    } else if (online) {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, device.is_on, true)}
            </div>
        `;
    } else {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, false, false)}
            </div>
        `;
    }

    const refreshBtn = !online ? `
        <button class="btn btn-outline-secondary btn-sm ms-2 refresh-device-btn"
                onclick="handleRefresh('${device.id}')" title="Refresh">
            &#x21bb;
        </button>
    ` : '';

    const updatedTime = device.last_updated ? formatTime(device.last_updated) : '';

    return `
        <div class="card device-card h-100 state-${stateClass}" data-id="${device.id}">
            <div class="card-header d-flex justify-content-between align-items-center">
                <div>
                    <strong>${escapeHtml(device.name)}</strong>
                    ${device.model ? `<div class="device-model">${escapeHtml(device.model)}</div>` : ''}
                </div>
                <div class="d-flex align-items-center">
                    <span class="status-badge">${online ? 'Online' : 'Offline'}</span>
                    ${updatedTime ? `<span class="last-updated ms-2">${updatedTime}</span>` : ''}
                    ${refreshBtn}
                </div>
            </div>
            <div class="card-body">
                ${bodyHtml}
            </div>
        </div>
    `;
}

function renderChildOutlet(deviceId, child, online) {
    const onClass = child.is_on ? 'is-on' : '';

    return `
        <div class="child-outlet ${onClass}">
            <div>
                <span class="outlet-name">${escapeHtml(child.alias)}</span>
                <span class="outlet-status">${online ? (child.is_on ? 'ON' : 'OFF') : ''}</span>
            </div>
            <div class="outlet-controls">
                ${renderToggleSwitch(deviceId, child.id, child.is_on, online)}
            </div>
        </div>
    `;
}

function renderToggleSwitch(deviceId, childId, isOn, enabled) {
    const childParam = childId !== null ? `'${childId}'` : 'null';
    const action = isOn ? 'off' : 'on';
    const onClass = isOn ? 'is-on' : '';
    const disabledAttr = enabled ? '' : 'disabled';

    return `
        <button class="toggle-switch ${onClass}"
                onclick="handleToggle('${deviceId}', '${action}', ${childParam})"
                title="${isOn ? 'Turn off' : 'Turn on'}"
                ${disabledAttr}>
        </button>
    `;
}

// === Event Handlers ===
async function loadDevices() {
    try {
        const data = await fetchDevices();
        renderDevices(data.devices);
    } catch (error) {
        console.error('Load devices error:', error);
        showToast('Failed to load devices: ' + error.message);
    }
}

async function handleToggle(deviceId, action, childId) {
    const card = document.querySelector(`[data-id="${deviceId}"]`);
    if (card) card.classList.add('loading');

    try {
        const result = await controlDevice(deviceId, action, childId);
        currentDevices[deviceId] = result;
        updateCardFromState(deviceId, result);
        const deviceName = result.name || deviceId;
        let msg = `${deviceName}: turned ${action}`;
        if (childId && result.children) {
            const child = result.children.find(c => c.id === childId);
            if (child) msg = `${deviceName} / ${child.alias}: turned ${action}`;
        }
        showToast(msg, 'success');
    } catch (error) {
        console.error('Toggle error:', error);
        const deviceName = currentDevices[deviceId]?.name || deviceId;
        let target = deviceName;
        if (childId) {
            const prev = currentDevices[deviceId];
            if (prev?.children) {
                const child = prev.children.find(c => c.id === childId);
                if (child) target = `${deviceName} / ${child.alias}`;
            }
        }
        showToast(`${target}: ${error.message}`);
    } finally {
        if (card) card.classList.remove('loading');
    }
}

async function handleRefresh(deviceId) {
    const card = document.querySelector(`[data-id="${deviceId}"]`);
    if (card) card.classList.add('loading');

    try {
        const result = await refreshDevice(deviceId);
        currentDevices[deviceId] = result;

        if (result.status === 'online') {
            showToast('Device reconnected', 'success');
        } else {
            showToast('Device still offline', 'warning');
        }

        await loadDevices();
    } catch (error) {
        console.error('Refresh error:', error);
        showToast('Refresh failed: ' + error.message);
    } finally {
        if (card) card.classList.remove('loading');
    }
}

// === State Updates ===
function updateCardFromState(deviceId, device) {
    const card = document.querySelector(`[data-id="${deviceId}"]`);
    if (!card) return;

    const online = device.status === 'online';
    const wasOnline = card.classList.contains('state-online');
    if (online !== wasOnline) {
        loadDevices();
        return;
    }

    if (!online) return;

    if (device.children && device.children.length > 0) {
        for (const child of device.children) {
            const buttons = card.querySelectorAll('.toggle-switch');
            for (const btn of buttons) {
                const onclick = btn.getAttribute('onclick') || '';
                if (onclick.includes(`'${child.id}'`)) {
                    updateToggleButton(btn, deviceId, child.id, child.is_on);
                    const outlet = btn.closest('.child-outlet');
                    if (outlet) {
                        outlet.classList.toggle('is-on', child.is_on);
                        const statusSpan = outlet.querySelector('.outlet-status');
                        if (statusSpan) statusSpan.textContent = child.is_on ? 'ON' : 'OFF';
                    }
                    break;
                }
            }
        }
    } else if (device.is_on !== undefined && device.is_on !== null) {
        const btn = card.querySelector('.single-device-control .toggle-switch');
        if (btn) {
            updateToggleButton(btn, deviceId, null, device.is_on);
        }
    }
}

function updateToggleButton(btn, deviceId, childId, isOn) {
    const action = isOn ? 'off' : 'on';
    const childParam = childId !== null ? `'${childId}'` : 'null';
    btn.classList.toggle('is-on', isOn);
    btn.setAttribute('onclick', `handleToggle('${deviceId}', '${action}', ${childParam})`);
    btn.setAttribute('title', isOn ? 'Turn off' : 'Turn on');
}

// === Polling ===
async function pollStatus() {
    try {
        const data = await fetchDevices();
        for (const device of data.devices) {
            const previous = currentDevices[device.id];

            if (previous && previous.status !== device.status) {
                renderDevices(data.devices);
                return;
            }

            currentDevices[device.id] = device;
            updateCardFromState(device.id, device);
        }
    } catch (error) {
        console.error('Poll error:', error);
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(pollStatus, POLL_INTERVAL);
}

// === Initialize ===
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    loadDevices();
    startPolling();
});
