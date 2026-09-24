(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  // Socket.IO is optional at runtime: if the CDN is blocked/offline, the app still works via HTTP.
  const socket = typeof window.io === 'function' ? window.io() : null;
  const state = { items: [], contacts: [] };

  const els = {
    history: document.getElementById('history'),
    empty: document.getElementById('empty-state'),
    search: document.getElementById('search-input'),
    input: document.getElementById('clipboard-input'),
    addClipboard: document.getElementById('add-clipboard-btn'),
    deviceName: document.getElementById('device-name'),
    deviceLabel: document.getElementById('device-label'),
    shareToggle: document.getElementById('share-toggle'),
    status: document.getElementById('composer-status'),
    contactId: document.getElementById('contact-id'),
    addContact: document.getElementById('add-contact-btn'),
    contacts: document.getElementById('contacts'),
    toast: document.getElementById('toast'),
    myId: document.getElementById('my-id'),
    copyId: document.getElementById('copy-id-btn'),
  };

  function getDeviceId() {
    let id = localStorage.getItem('mysync-device-id');
    if (!id) {
      if (crypto.randomUUID) {
        id = crypto.randomUUID();
      } else {
        id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      }
      localStorage.setItem('mysync-device-id', id);
    }
    return id;
  }

  const deviceId = getDeviceId();
  let deviceName = localStorage.getItem('mysync-device-name') || '';
  if (!deviceName) {
    deviceName = navigator.userAgentData?.platform || navigator.platform || 'My device';
    localStorage.setItem('mysync-device-name', deviceName);
  }
  els.deviceName.value = deviceName;
  els.deviceLabel.textContent = deviceName;

  function escapeHtml(value) {
    return value.replace(/[&<>'"]/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[char]));
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  function toast(message, error = false) {
    els.toast.textContent = message;
    els.toast.className = `toast show${error ? ' toast-error' : ''}`;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => els.toast.className = 'toast', 2600);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
        ...(options.headers || {}),
      },
    });
    if (response.status === 401) {
      window.location.href = '/login';
      return null;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }
    return data;
  }

  async function loadData() {
    try {
      const data = await api('/api/bootstrap');
      if (!data) return;
      state.items = data.items;
      state.contacts = data.contacts;
      renderContacts();
      renderHistory();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderContacts() {
    if (!state.contacts.length) {
      els.contacts.innerHTML = '<div class="tiny muted contact-empty">No contacts yet.</div>';
      return;
    }
    els.contacts.innerHTML = state.contacts.map(contact => `
      <div class="contact-row">
        <div>
          <strong>${escapeHtml(contact.display_name)}</strong>
          <div class="tiny muted">${escapeHtml(contact.public_id)}</div>
        </div>
        <button class="icon-btn remove-contact" type="button" data-id="${contact.id}" title="Remove">×</button>
      </div>
    `).join('');

    document.querySelectorAll('.remove-contact').forEach(button => {
      button.addEventListener('click', async () => {
        try {
          await api(`/api/contacts/${button.dataset.id}`, { method: 'DELETE' });
          toast('Contact removed.');
          await loadData();
        } catch (error) {
          toast(error.message, true);
        }
      });
    });
  }

  function groupItems(items) {
    const owners = new Map();
    for (const item of items) {
      const ownerKey = item.owner.public_id;
      if (!owners.has(ownerKey)) {
        owners.set(ownerKey, { owner: item.owner, devices: new Map() });
      }
      const ownerGroup = owners.get(ownerKey);
      if (!ownerGroup.devices.has(item.device_id)) {
        ownerGroup.devices.set(item.device_id, {
          name: item.device_name,
          items: [],
          mine: item.is_mine,
        });
      }
      const device = ownerGroup.devices.get(item.device_id);
      device.name = item.device_name;
      device.items.push(item);
    }
    return [...owners.values()];
  }

  function renderHistory() {
    const query = els.search.value.trim().toLowerCase();
    const filtered = state.items.filter(item => {
      if (!query) return true;
      return [
        item.content,
        item.device_name,
        item.owner.display_name,
        item.owner.public_id,
      ].some(value => value.toLowerCase().includes(query));
    });

    els.empty.classList.toggle('hidden', filtered.length !== 0);
    if (!filtered.length) {
      els.history.innerHTML = '';
      return;
    }

    const ownerGroups = groupItems(filtered);
    els.history.innerHTML = ownerGroups.map(group => {
      const ownerTitle = group.owner.public_id === els.myId.textContent
        ? 'Your clipboards'
        : `${escapeHtml(group.owner.display_name)}'s shared clipboards`;

      const devices = [...group.devices.values()].map(device => `
        <details class="device-group" open>
          <summary>
            <div>
              <span class="device-title">${escapeHtml(device.name)}</span>
              <span class="tiny muted">${device.items.length} item${device.items.length === 1 ? '' : 's'}</span>
            </div>
            <span class="chevron">⌄</span>
          </summary>
          <div class="clip-list">
            ${device.items.map(item => `
              <article class="clip-card">
                <div class="clip-topline">
                  <div class="clip-meta">
                    <span>${escapeHtml(formatDate(item.created_at))}</span>
                    ${item.is_shared ? '<span class="shared-badge">Shared</span>' : '<span class="private-badge">Private</span>'}
                  </div>
                  <div class="clip-actions">
                    <button class="secondary-btn copy-clip" type="button" data-id="${item.id}">Copy</button>
                    ${item.is_mine ? `<button class="secondary-btn toggle-share" type="button" data-id="${item.id}" data-shared="${item.is_shared}">${item.is_shared ? 'Unshare' : 'Share'}</button>` : ''}
                    ${item.is_mine ? `<button class="danger-btn delete-clip" type="button" data-id="${item.id}">Delete</button>` : ''}
                  </div>
                </div>
                <pre class="clip-content">${escapeHtml(item.content)}</pre>
                ${!item.is_mine ? `<div class="tiny muted">Shared by ${escapeHtml(item.owner.display_name)} · ${escapeHtml(item.owner.public_id)}</div>` : ''}
              </article>
            `).join('')}
          </div>
        </details>
      `).join('');

      return `<section class="owner-section">
        <div class="owner-heading">
          <div>
            <div class="section-kicker">${group.owner.public_id === els.myId.textContent ? 'YOU' : 'CONTACT'}</div>
            <h3>${ownerTitle}</h3>
            ${group.owner.public_id !== els.myId.textContent ? `<div class="tiny muted">${escapeHtml(group.owner.public_id)}</div>` : ''}
          </div>
        </div>
        ${devices}
      </section>`;
    }).join('');

    bindHistoryActions();
  }

  function bindHistoryActions() {
    document.querySelectorAll('.copy-clip').forEach(button => {
      button.addEventListener('click', async () => {
        const item = state.items.find(entry => String(entry.id) === button.dataset.id);
        if (!item) return;
        try {
          await navigator.clipboard.writeText(item.content);
          toast('Copied to your clipboard.');
        } catch (_) {
          toast('The browser blocked clipboard access.', true);
        }
      });
    });

    document.querySelectorAll('.toggle-share').forEach(button => {
      button.addEventListener('click', async () => {
        const currentlyShared = button.dataset.shared === 'true';
        try {
          await api(`/api/items/${button.dataset.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_shared: !currentlyShared }),
          });
          toast(currentlyShared ? 'Clipboard item unshared.' : 'Clipboard item shared with contacts.');
          await loadData();
        } catch (error) {
          toast(error.message, true);
        }
      });
    });

    document.querySelectorAll('.delete-clip').forEach(button => {
      button.addEventListener('click', async () => {
        try {
          await api(`/api/items/${button.dataset.id}`, { method: 'DELETE' });
          toast('Clipboard item deleted.');
          await loadData();
        } catch (error) {
          toast(error.message, true);
        }
      });
    });
  }

  async function addClipboard() {
    const content = els.input.value;
    const name = els.deviceName.value.trim() || 'Unnamed device';
    if (!content.trim()) {
      toast('Enter some text first.', true);
      return;
    }

    els.addClipboard.disabled = true;
    els.status.textContent = 'Saving…';
    localStorage.setItem('mysync-device-name', name);
    els.deviceLabel.textContent = name;

    try {
      await api('/api/items', {
        method: 'POST',
        body: JSON.stringify({
          content,
          device_id: deviceId,
          device_name: name,
          is_shared: els.shareToggle.checked,
        }),
      });
      els.input.value = '';
      toast(els.shareToggle.checked ? 'Saved and shared with contacts.' : 'Saved privately.');
      els.status.textContent = '';
      await loadData();
    } catch (error) {
      els.status.textContent = '';
      toast(error.message, true);
    } finally {
      els.addClipboard.disabled = false;
    }
  }

  async function addContact() {
    const publicId = els.contactId.value.trim();
    if (!publicId) {
      toast('Enter a MySync ID.', true);
      return;
    }
    els.addContact.disabled = true;
    try {
      await api('/api/contacts', {
        method: 'POST',
        body: JSON.stringify({ public_id: publicId }),
      });
      els.contactId.value = '';
      toast('Person added.');
      await loadData();
    } catch (error) {
      toast(error.message, true);
    } finally {
      els.addContact.disabled = false;
    }
  }

  els.addClipboard.addEventListener('click', addClipboard);
  els.addContact.addEventListener('click', addContact);
  els.search.addEventListener('input', renderHistory);
  els.input.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') addClipboard();
  });
  els.deviceName.addEventListener('input', () => {
    const value = els.deviceName.value.trim();
    els.deviceLabel.textContent = value || 'Device';
  });
  els.copyId.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(els.myId.textContent);
      toast('MySync ID copied.');
    } catch (_) {
      toast('The browser blocked clipboard access.', true);
    }
  });
  els.contactId.addEventListener('keydown', event => {
    if (event.key === 'Enter') addContact();
  });

  if (socket) {
    socket.on('connect', () => loadData());
    socket.on('data_changed', () => loadData());
  }
  loadData();
})();
