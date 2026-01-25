/**
 * Kasa Web Controller - Frontend Application
 */

// === API Functions ===
async function fetchDevices() {
    const response = await fetch('/api/devices');
    if (!response.ok) throw new Error('Failed to fetch devices');
    return response.json();
}

async function controlDevice(deviceId, action, childId = null) {
    const body = { action };
    if (childId !== null) body.child_id = childId;

    const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (!response.ok) {
        const error = await response.json();
        // Handle structured error response
        if (error.detail && typeof error.detail === 'object') {
            throw new Error(error.detail.message || 'Control failed');
        }
        throw new Error(error.detail || 'Control failed');
    }
    return response.json();
}

async function refreshDevice(deviceId) {
    const response = await fetch(`/api/devices/${encodeURIComponent(deviceId)}/refresh`, {
        method: 'POST'
    });
    if (!response.ok) throw new Error('Refresh failed');
    return response.json();
}

// === UI Functions ===
function showAlert(message, type = 'danger') {
    const container = document.getElementById('alert-container');
    const alert = document.createElement('div');
    alert.className = `alert alert-${type} alert-dismissible fade show`;
    alert.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    container.appendChild(alert);
    setTimeout(() => alert.remove(), 5000);
}

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

    container.innerHTML = devices.map(device => renderDeviceCard(device)).join('');
}

function getDeviceState(device) {
    // Handle three status types: online, temp_unavailable, offline
    if (device.status === 'offline') return 'offline';
    if (device.status === 'temp_unavailable') return 'connecting';
    if (device.status !== 'online') return 'offline';

    // Online device - check power state
    if (device.is_strip && device.children) {
        const onCount = device.children.filter(c => c.is_on).length;
        if (onCount === 0) return 'off';
        if (onCount === device.children.length) return 'on';
        return 'mixed';
    }
    return device.is_on ? 'on' : 'off';
}

function getStatusText(state) {
    const texts = {
        on: 'ON',
        off: 'OFF',
        offline: 'Offline',
        connecting: 'Connecting...',
        mixed: 'Mixed'
    };
    return texts[state] || state;
}

function isDeviceOnline(device) {
    return device.status === 'online';
}

function renderDeviceCard(device) {
    const state = getDeviceState(device);
    const statusText = getStatusText(state);
    const online = isDeviceOnline(device);

    let bodyHtml = '';
    if (device.is_strip && device.children && device.children.length > 0) {
        bodyHtml = device.children.map(child => renderChildOutlet(device.id, child, online, device.last_state)).join('');
    } else if (online) {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, device.is_on)}
            </div>
        `;
    } else {
        // Offline - show last state if available
        const lastState = device.last_state;
        if (lastState) {
            const lastUpdated = lastState.last_updated ? new Date(lastState.last_updated).toLocaleString() : '';
            bodyHtml = `
                <div class="offline-message">
                    <div>Device is ${device.status === 'temp_unavailable' ? 'temporarily unavailable' : 'offline'}</div>
                    ${lastUpdated ? `<small class="text-muted">Last seen: ${lastUpdated}</small>` : ''}
                    <div class="mt-2">
                        <small>Last state: ${lastState.is_on ? 'ON' : 'OFF'}</small>
                    </div>
                </div>
            `;
        } else {
            bodyHtml = `<div class="offline-message">Device is offline</div>`;
        }
    }

    // Add refresh button for offline devices
    const refreshBtn = !online ? `
        <button class="btn btn-outline-secondary btn-sm ms-2" onclick="handleRefresh('${device.id}')" title="Refresh">
            &#x21bb;
        </button>
    ` : '';

    return `
        <div class="card device-card state-${state}" data-id="${device.id}">
            <div class="card-header d-flex justify-content-between align-items-center">
                <div>
                    <strong>${escapeHtml(device.name)}</strong>
                    ${device.model ? `<div class="device-model">${escapeHtml(device.model)}</div>` : ''}
                </div>
                <div class="d-flex align-items-center">
                    <span class="status-badge">${statusText}</span>
                    ${refreshBtn}
                </div>
            </div>
            <div class="card-body">
                ${device.error ? `<div class="alert alert-warning m-2 py-1 px-2"><small>${escapeHtml(device.error)}</small></div>` : ''}
                ${bodyHtml}
            </div>
        </div>
    `;
}

function renderChildOutlet(deviceId, child, online, lastState = null) {
    // For online devices, use current state; for offline, try to get from last_state
    let isOn = child.is_on;
    let statusKnown = online;

    if (!online && lastState && lastState.children) {
        const lastChild = lastState.children.find(c => c.id === child.id);
        if (lastChild) {
            isOn = lastChild.is_on;
            statusKnown = true;
        }
    }

    const onClass = isOn ? 'is-on' : '';
    const staleClass = !online && statusKnown ? 'stale' : '';

    return `
        <div class="child-outlet ${onClass} ${staleClass}">
            <div>
                <span class="outlet-name">${escapeHtml(child.alias)}</span>
                <span class="outlet-status">${statusKnown ? (isOn ? 'ON' : 'OFF') : '?'}</span>
            </div>
            <div class="outlet-controls">
                ${online ? renderToggleSwitch(deviceId, child.id, isOn) : '<span class="text-muted">Offline</span>'}
            </div>
        </div>
    `;
}

function renderToggleSwitch(deviceId, childId, isOn) {
    const childParam = childId !== null ? `'${childId}'` : 'null';
    const action = isOn ? 'off' : 'on';
    const onClass = isOn ? 'is-on' : '';
    return `
        <button class="toggle-switch ${onClass}"
                onclick="handleToggle('${deviceId}', '${action}', ${childParam})"
                title="${isOn ? 'Turn off' : 'Turn on'}">
        </button>
    `;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// === Event Handlers ===
async function loadDevices() {
    try {
        const data = await fetchDevices();
        renderDevices(data.devices);
    } catch (error) {
        console.error('Load devices error:', error);
        showAlert('Failed to load devices: ' + error.message);
    }
}

async function handleToggle(deviceId, action, childId) {
    const card = document.querySelector(`[data-id="${deviceId}"]`);
    if (card) card.classList.add('loading');

    try {
        await controlDevice(deviceId, action, childId);
        await loadDevices();
    } catch (error) {
        console.error('Toggle error:', error);
        showAlert('Operation failed: ' + error.message);
    } finally {
        if (card) card.classList.remove('loading');
    }
}

async function handleRefresh(deviceId) {
    const card = document.querySelector(`[data-id="${deviceId}"]`);
    if (card) card.classList.add('loading');

    try {
        const result = await refreshDevice(deviceId);
        if (result.success) {
            showAlert('Device reconnected', 'success');
        } else {
            showAlert(result.error || 'Device still offline', 'warning');
        }
        await loadDevices();
    } catch (error) {
        console.error('Refresh error:', error);
        showAlert('Refresh failed: ' + error.message);
    } finally {
        if (card) card.classList.remove('loading');
    }
}

// === Initialize ===
document.addEventListener('DOMContentLoaded', () => {
    loadDevices();
});
