/**
 * Kasa Web Controller - Frontend Application
 */

// === API Functions ===
async function fetchDevices() {
    const response = await fetch('/api/devices');
    if (!response.ok) throw new Error('Failed to fetch devices');
    return response.json();
}

async function toggleDevice(deviceId, action, childId = null) {
    const body = { action };
    if (childId !== null) body.child_id = childId;

    const response = await fetch(`/api/device/${encodeURIComponent(deviceId)}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Control failed');
    }
    return response.json();
}

async function forceDiscoverAPI() {
    const response = await fetch('/api/discover', { method: 'POST' });
    if (!response.ok) throw new Error('Discovery failed');
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
    if (!device.online) return 'offline';
    if (device.is_strip && device.children) {
        const onCount = device.children.filter(c => c.is_on).length;
        if (onCount === 0) return 'off';
        if (onCount === device.children.length) return 'on';
        return 'mixed';
    }
    return device.is_on ? 'on' : 'off';
}

function getStatusText(state) {
    const texts = { on: 'ON', off: 'OFF', offline: 'Offline', mixed: 'Mixed' };
    return texts[state] || state;
}

function renderDeviceCard(device) {
    const state = getDeviceState(device);
    const statusText = getStatusText(state);

    let bodyHtml = '';
    if (device.is_strip && device.children && device.children.length > 0) {
        bodyHtml = device.children.map(child => renderChildOutlet(device.id, child, device.online)).join('');
    } else if (device.online) {
        bodyHtml = `
            <div class="single-device-control">
                ${renderToggleSwitch(device.id, null, device.is_on)}
            </div>
        `;
    } else {
        bodyHtml = `<div class="offline-message">Device is offline</div>`;
    }

    return `
        <div class="card device-card state-${state}" data-id="${device.id}">
            <div class="card-header d-flex justify-content-between align-items-center">
                <div>
                    <strong>${escapeHtml(device.name)}</strong>
                    ${device.model ? `<div class="device-model">${escapeHtml(device.model)}</div>` : ''}
                </div>
                <span class="status-badge">${statusText}</span>
            </div>
            <div class="card-body">
                ${device.error ? `<div class="alert alert-warning m-2 py-1 px-2"><small>${escapeHtml(device.error)}</small></div>` : ''}
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
                <span class="outlet-status">${child.is_on ? 'ON' : 'OFF'}</span>
            </div>
            <div class="outlet-controls">
                ${online ? renderToggleSwitch(deviceId, child.id, child.is_on) : '<span class="text-muted">Offline</span>'}
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
        await toggleDevice(deviceId, action, childId);
        await loadDevices();
    } catch (error) {
        console.error('Toggle error:', error);
        showAlert('Operation failed: ' + error.message);
    } finally {
        if (card) card.classList.remove('loading');
    }
}

async function forceDiscover() {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Discovering...';

    try {
        await forceDiscoverAPI();
        showAlert('Device discovery completed', 'success');
        await loadDevices();
    } catch (error) {
        console.error('Discover error:', error);
        showAlert('Discovery failed: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Discover';
    }
}

// === Initialize ===
document.addEventListener('DOMContentLoaded', () => {
    loadDevices();
});
