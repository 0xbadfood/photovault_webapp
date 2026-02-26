const state = {
    user: null, // { userid: '...' }
    config: null,
    view: localStorage.getItem('activeView') || 'dashboard', // 'dashboard' | 'files' | 'photos' | 'screenshots' | 'videos' | 'people' | 'search' | 'discover' | 'albums'
    dashboardStats: null,


    // File Explorer State
    currentPath: 'web/files', // Relative path from user root
    explorerItems: [], // Items in currentPath
    fileViewMode: 'grid', // 'list' | 'grid'
    selectedFiles: new Set(), // Set of filenames
    sortBy: localStorage.getItem('sortBy') || 'name', // 'name' | 'date' | 'size'
    sortOrder: localStorage.getItem('sortOrder') || 'asc', // 'asc' | 'desc'
    fileExplorerLimit: 100, // Pagination limit

    // Photos View State
    devices: [], // Aggregated scan result

    // Timeline State (for Photos tab)
    timelineGroups: [], // [{ date, count, photos }]
    photoSearchQuery: '', // Search query for photo descriptions/tags

    // People View State
    people: [], // [{id, name, thumbnail, photo_count}]
    currentPerson: null, // { id, name, thumbnail } — person detail view
    personPhotos: [], // Photos for current person detail view

    // Search View State
    searchQuery: '',
    searchPersonIds: [], // Selected person IDs for filter
    searchResults: [], // [{id, thumbnail_url, image_url, description}]

    // Albums State
    albums: [], // [{id, name, description, album_type, photo_count, cover_url}]
    currentAlbum: null, // { id, name, photos: [] }
    selectedAlbums: new Set(), // Set of album IDs
    albumSelectionMode: false,

    // Discover State
    memories: [], // [{type, title, description, photos}]

    // Sharing State
    sharedPhotos: {}, // { ownerEmail: [photos] }

    // Selection Mode
    selectedPhotos: new Set(), // Set of photo IDs
    selectionMode: false,

    loading: false,
    viewerImage: null, // { src: string } or null
    viewerList: [], // Array of media items for navigation
    viewerIndex: -1, // Current index in viewerList
    savedScrollPosition: 0, // Saved scroll position for restore after viewer close
    videoVolume: 1.0,
    zoomLevel: 100, // %
    panX: 0,
    panY: 0,
    isDragging: false,
    dragStartX: 0,
    dragStartY: 0
};

function updateZoom(val) {
    updateZoomUI(val);
}

// --- API Calls ---

async function login(userid, password) {
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userid, password })
        });
        const data = await res.json();
        if (res.ok) {
            if (data.is_admin) {
                window.location.href = '/admin';
                return;
            }
            state.user = { userid: data.userid, role: data.role || 'user', hosts: data.hosts || [], force_change: data.force_change };

            try {
                const configRes = await fetch('/api/config');
                if (configRes.ok) state.config = await configRes.json();
            } catch (e) {
                state.config = { port: 8877, ai: "YES", search: "YES", people: "YES", discover: "YES" };
            }

            // Initialize views in background
            scanFiles();
            await fetchDashboardStats();
            const media = state.dashboardStats?.media || {};
            const hasMedia = (media.photos > 0) || (media.videos > 0) || (media.albums > 0) || (media.screenshots > 0);
            state.view = hasMedia ? 'photos' : 'upload';
            render(); // Render immediately

            fetchExplorer('');
            fetchPeople();
            fetchAlbums(false); // Pre-fetch albums
        } else {
            alert(data.error || 'Login failed');
        }
    } catch (e) {
        alert('Internal Error');
        console.error(e);
    }
}



async function scanFiles() {
    try {
        const res = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.devices) {
            state.devices = data.devices;
        }
    } catch (e) {
        console.error("Scan failed", e);
    }
}

async function fetchExplorer(path) {
    state.loading = true;
    render();
    try {
        const url = `/api/files/list?path=${encodeURIComponent(path)}`;
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok) {
            state.explorerItems = data.items;
            state.currentPath = data.path; // server returns the resolved path
            state.fileExplorerLimit = 100; // Reset pagination on new fetch
        } else {
            console.error("Fetch explorer failed", data.error);
        }
    } catch (e) {
        console.error("Fetch error", e);
    } finally {
        state.loading = false;
        render();
    }
}

async function fetchPeople() {
    try {
        const res = await fetch('/api/people');
        const data = await res.json();
        if (data.people) {
            state.people = data.people;
        }
    } catch (e) {
        console.error(e);
    } finally {
        if (state.view === 'people' || state.view === 'search') {
            render();
        }
    }
}

async function fetchPersonPhotos(personId) {
    state.loading = true;
    render();
    try {
        const res = await fetch(`/api/people/${personId}/photos`);
        const data = await res.json();
        if (data.person) {
            state.currentPerson = data.person;
        }
        state.personPhotos = data.photos || [];
    } catch (e) {
        console.error(e);
        state.personPhotos = [];
    } finally {
        state.loading = false;
        render();
    }
}

async function updatePersonName(id, name) {
    try {
        await fetch('/api/people/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, name })
        });
        await fetchPeople();
        // Update currentPerson name if in detail view
        if (state.currentPerson && state.currentPerson.id === id) {
            state.currentPerson.name = name;
        }
        render();
    } catch (e) { console.error(e); }
}

async function deletePerson(id, name) {
    if (!confirm(`Are you sure you want to delete ${name}?`)) return;
    try {
        const res = await fetch('/api/people/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        if (res.ok) {
            // If we were viewing this person, go back to grid
            if (state.currentPerson && state.currentPerson.id === id) {
                state.currentPerson = null;
                state.personPhotos = [];
            }
            await fetchPeople();
            render();
        } else {
            const data = await res.json();
            alert(data.error || 'Delete failed');
        }
    } catch (e) {
        alert('Delete error');
        console.error(e);
    }
}

// --- Sharing Functions ---
// (Legacy sharing removed) rápido

async function runSearch() {
    state.loading = true;
    render();
    try {
        const res = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                person_ids: state.searchPersonIds,
                description: state.searchQuery
            })
        });
        const data = await res.json();
        if (data.results) {
            state.searchResults = data.results;
        }
    } catch (e) { console.error(e); }
    finally {
        state.loading = false;
        render();
    }
}

async function fetchTimeline(type) {
    state.loading = true;
    render();
    try {
        let url = `/api/timeline`;
        let sep = '?';
        if (type) {
            url += `${sep}type=${encodeURIComponent(type)}`;
            sep = '&';
        }
        if (state.photoSearchQuery) {
            url += `${sep}search=${encodeURIComponent(state.photoSearchQuery)}`;
            sep = '&';
        }
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok && data.groups) {
            state.timelineGroups = data.groups;
        }
    } catch (e) {
        console.error('Timeline fetch error', e);
    } finally {
        state.loading = false;
        render();
    }
}

async function fetchAlbums(shouldRender = true) {
    try {
        const res = await fetch('/api/albums');
        const data = await res.json();
        if (data.albums) {
            state.albums = data.albums;
            if (shouldRender) render();
        }
    } catch (e) {
        console.error('Albums fetch error', e);
    } finally {
        if (shouldRender) render();
    }
}

async function fetchAlbumPhotos(albumId, isSharedAlbumMeta = null) {
    state.loading = true;
    render();
    try {
        const url = `/api/albums/${albumId}/photos`;
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok && data.photos) {
            if (isSharedAlbumMeta) {
                state.currentAlbum = {
                    id: albumId,
                    name: isSharedAlbumMeta.asset_title || `Shared Album ${albumId}`,
                    photos: data.photos,
                    album_type: 'shared'
                };
            } else {
                // Find album metadata from state.albums
                const albumMeta = state.albums.find(a => a.id === albumId);
                state.currentAlbum = {
                    id: albumId,
                    name: albumMeta ? albumMeta.name : 'Unknown Album',
                    photos: data.photos,
                    album_type: albumMeta ? albumMeta.album_type : 'manual'
                };
            }
        } else if (res.status === 404 || res.status === 401) {
            alert(data.error || "Album not found or access denied.");
        }
    } catch (e) {
        console.error('Album photos fetch error', e);
    } finally {
        state.loading = false;
        render();
    }
}

async function fetchMemories() {
    state.loading = true;
    render();
    try {
        const url = `/api/discover/memories`;
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok && data.memories) {
            state.memories = data.memories;
        }
    } catch (e) {
        console.error('Memories fetch error', e);
    } finally {
        state.loading = false;
        render();
    }
}

async function fetchDashboardStats() {
    try {
        const res = await fetch(`/api/dashboard/stats`);
        if (res.ok) {
            const data = await res.json();
            if (!data.error) {
                state.dashboardStats = data;
                render();
            } else {
                console.error('Dashboard stats error:', data.error);
                state.dashboardStats = { error: data.error };
                render();
            }
        } else {
            state.dashboardStats = { error: `HTTP ${res.status}` };
            render();
        }
    } catch (e) {
        console.error('Dashboard stats fetch error', e);
        state.dashboardStats = { error: 'Network Error' };
        render();
    }
}

function refreshViewData() {
    if (!state.user) return;
    const view = state.view;
    if (view === 'photos') fetchTimeline('photo');
    else if (view === 'screenshots') fetchTimeline('screenshot');
    else if (view === 'videos') fetchTimeline('video');
    else if (view === 'people' || view === 'search') fetchPeople();
    else if (view === 'discover') fetchMemories();
    else if (view === 'albums') fetchAlbums();
    else if (view === 'shared') fetchSharedPhotos();
    else if (view === 'files') fetchExplorer(state.currentPath || 'web/files');
    else if (view === 'dashboard') fetchDashboardStats();
}



// --- Selection Mode Helpers ---

function togglePhotoSelection(photoId) {
    if (state.selectedPhotos.has(photoId)) {
        state.selectedPhotos.delete(photoId);
    } else {
        state.selectedPhotos.add(photoId);
    }
    if (state.selectedPhotos.size === 0) {
        state.selectionMode = false;
    }
    document.body.classList.toggle('selection-active', state.selectionMode);
}

function clearSelection() {
    state.selectedPhotos.clear();
    state.selectionMode = false;
    document.body.classList.remove('selection-active');
    render();
}

/** Update selection UI in-place — no full re-render, no flicker */
function updateSelectionUI(photoId) {
    const isSelected = state.selectedPhotos.has(photoId);

    // Update the photo tile
    const item = document.querySelector(`.photo-item[data-photo-id="${photoId}"]`);
    if (item) {
        item.classList.toggle('selected', isSelected);
        const check = item.querySelector('.photo-select-check');
        if (check) {
            check.innerHTML = isSelected ? '<i class="fa-solid fa-check"></i>' : '';
        }
    }

    // Update toolbar count
    const countEl = document.querySelector('.selection-toolbar-count');
    if (countEl) {
        const n = state.selectedPhotos.size;
        countEl.textContent = `${n} selected`;
    }

    // Show/hide toolbar
    const toolbar = document.querySelector('.selection-toolbar');
    if (toolbar) {
        toolbar.classList.toggle('visible', state.selectionMode && state.selectedPhotos.size > 0);
    }

    // If nothing selected, clear and do a full render to hide the toolbar/overlays
    if (state.selectedPhotos.size === 0) {
        clearSelection();
    }
}

function SelectionToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'selection-toolbar' + (state.selectionMode && state.selectedPhotos.size > 0 ? ' visible' : '');

    const count = state.selectedPhotos.size;
    const countEl = document.createElement('span');
    countEl.className = 'selection-toolbar-count';
    countEl.textContent = `${count} selected`;
    toolbar.appendChild(countEl);

    const divider = document.createElement('div');
    divider.className = 'selection-toolbar-divider';
    toolbar.appendChild(divider);

    // Add to Album
    const albumBtn = document.createElement('button');
    albumBtn.className = 'sel-btn sel-btn-album';
    albumBtn.innerHTML = '<i class="fa-solid fa-book"></i> Add to Album';
    albumBtn.onclick = () => {
        const ids = [...state.selectedPhotos];
        if (ids.length === 0) return;
        openAddToAlbumModal(ids); // Pass ALL selected IDs
        clearSelection();
    };
    toolbar.appendChild(albumBtn);

    // Cancel selection
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'sel-btn sel-btn-cancel';
    cancelBtn.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel';
    cancelBtn.onclick = clearSelection;
    toolbar.appendChild(cancelBtn);

    return toolbar;
}

async function createAlbum(name, description = '', shouldRender = true) {

    try {
        const res = await fetch('/api/albums/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, album_type: 'manual' })
        });
        const data = await res.json();
        if (res.ok) {
            await fetchAlbums(shouldRender);
            return data.album_id;
        } else {
            alert(data.error || 'Failed to create album');
        }
    } catch (e) {
        console.error('Create album error', e);
        alert('Error creating album');
    }
    return null;
}

async function deleteAlbum(albumId) {
    if (!confirm('Delete this album? Photos will not be deleted.')) return;
    try {
        const res = await fetch(`/api/albums/${albumId}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            await fetchAlbums();
        } else {
            const data = await res.json();
            alert(data.error || 'Failed to delete album');
        }
    } catch (e) {
        console.error('Delete album error', e);
        alert('Error deleting album');
    }
}

async function renameItem(name, newName) {
    // path arg for API is full relative path: currentPath + / + name
    const fullPath = state.currentPath ? `${state.currentPath}/${name}` : name;

    try {
        const res = await fetch('/api/files/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: fullPath, new_name: newName })
        });
        if (res.ok) {
            await fetchExplorer(state.currentPath); // Refresh folder
        } else {
            const d = await res.json();
            alert(d.error || 'Rename failed');
        }
    } catch (e) {
        alert('Rename error');
    }
}

async function deleteItem(name) {
    if (!confirm(`Delete "${name}"?`)) return;
    const fullPath = state.currentPath ? `${state.currentPath}/${name}` : name;

    try {
        const res = await fetch('/api/files/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: fullPath })
        });
        if (res.ok) {
            await fetchExplorer(state.currentPath);
        } else {
            const d = await res.json();
            alert(d.error || 'Delete failed');
        }
    } catch (e) {
        alert('Delete error');
    }
}

async function batchDeleteFiles() {
    if (state.selectedFiles.size === 0) return;
    if (!confirm(`Delete ${state.selectedFiles.size} items?`)) return;

    const paths = Array.from(state.selectedFiles).map(name =>
        state.currentPath ? `${state.currentPath}/${name}` : name
    );

    try {
        const res = await fetch('/api/files/batch-delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths })
        });

        const data = await res.json();
        if (res.ok) {
            state.selectedFiles.clear();
            await fetchExplorer(state.currentPath);
            if (data.errors && data.errors.length > 0) {
                alert(`Some items could not be deleted:\n${data.errors.join('\n')}`);
            }
        } else {
            alert(data.error || 'Batch delete failed');
        }
    } catch (e) {
        console.error(e);
        alert('Batch delete error');
    }
}

async function downloadSelectedFiles() {
    if (state.selectedFiles.size === 0) return;
    const paths = Array.from(state.selectedFiles).map(name =>
        state.currentPath ? `${state.currentPath}/${name}` : name
    );

    try {
        const res = await fetch('/api/files/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths })
        });

        if (res.ok) {
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'download.zip';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } else {
            const data = await res.json();
            alert(data.error || 'Download failed');
        }
    } catch (e) {
        console.error(e);
        alert('Download error');
    }
}


function openAddToAlbumModal(items) {
    const photoIds = Array.isArray(items) ? items : [items];

    // Remove existing modal if any
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';

    const modal = document.createElement('div');
    modal.className = 'modal';

    // Header
    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Add ${photoIds.length} Photo${photoIds.length > 1 ? 's' : ''} to Album</div>
            <button class="modal-close"><i class="fa-solid fa-times"></i></button>
        </div>
        <div class="modal-body">
            <div class="album-list" id="modalAlbumList">
                <div style="text-align:center; color:var(--text-secondary); padding:10px;">Loading albums...</div>
            </div>
            <div class="create-album-section">
                <div class="create-album-input-group">
                    <input type="text" id="newAlbumName" placeholder="New Album Name">
                    <button class="create-album-btn" id="createNewAlbumBtn">Create</button>
                </div>
            </div>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Close handlers
    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => {
        if (e.target === overlay) close();
    };

    // Load albums
    const loadAlbums = async () => {
        const listContainer = modal.querySelector('#modalAlbumList');
        // Ensure we have latest albums
        await fetchAlbums(false); // Updates state.albums

        listContainer.innerHTML = '';
        const ownAlbums = state.albums.filter(a => a.album_type !== 'shared');

        if (ownAlbums.length === 0) {
            listContainer.innerHTML = `<div style="text-align:center; color:var(--text-secondary); padding:10px;">No editable albums found.</div>`;
        }

        ownAlbums.forEach(album => {
            const item = document.createElement('div');
            item.className = 'album-list-item';

            // Cover logic
            let thumbHtml = '';
            if (album.cover_url) {
                thumbHtml = `<img src="${album.cover_url}" class="album-list-thumb">`;
            } else {
                thumbHtml = `<div class="album-list-thumb" style="display:flex;align-items:center;justify-content:center"><i class="fa-solid fa-images" style="font-size:16px;color:#888"></i></div>`;
            }

            item.innerHTML = `
                ${thumbHtml}
                <div class="album-list-info">
                    <div class="album-list-name">${album.name}</div>
                    <div class="album-list-count">${album.photo_count} photos</div>
                </div>
                <i class="fa-solid fa-plus-circle" style="color:var(--accent-color)"></i>
            `;

            item.onclick = async () => {
                // Add to album
                try {
                    const res = await fetch(`/api/albums/${album.id}/add-photos`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ photo_ids: photoIds })
                    });
                    if (res.ok) {
                        // success
                        close();
                        // Optional: show toast
                        // alert(`Added to ${album.name}`);
                        // Refresh if we are in album view?
                        if (state.view === 'albums' && state.currentAlbum && state.currentAlbum.id === album.id) {
                            fetchAlbumPhotos(album.id);
                        }
                    } else {
                        alert('Failed to add photos');
                    }
                } catch (e) {
                    console.error(e);
                    alert('Error adding photos');
                }
            };
            listContainer.appendChild(item);
        });
    };

    loadAlbums();

    // Create New Handler
    const createBtn = modal.querySelector('#createNewAlbumBtn');
    const nameInput = modal.querySelector('#newAlbumName');

    const doCreate = async () => {
        const name = nameInput.value.trim();
        if (!name) return;

        const albumId = await createAlbum(name, '', false); // Helper calls fetchAlbums internally
        if (albumId) {
            // Add photo to new album
            try {
                await fetch(`/api/albums/${albumId}/add-photos`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ photo_ids: photoIds })
                });
                close();
                // alert(`Created ${name} and added photo`);
            } catch (e) {
                console.error(e);
                alert('Created album but failed to add photos');
            }
        }
    };

    createBtn.onclick = doCreate;
    nameInput.onkeypress = (e) => {
        if (e.key === 'Enter') doCreate();
    };
}

// --- Components ---

function LoginScreen() {
    const div = document.createElement('div');
    div.className = 'login-container';
    div.innerHTML = `
        <div class="login-box">
            <img src="/static/logo.png" style="width: 80px; margin-bottom: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
            <h1>Photovault</h1>
            <div id="userLoginSection">
                <div class="input-group">
                    <input type="text" id="userid" placeholder="User ID">
                </div>
                <div class="input-group">
                    <input type="password" id="password" placeholder="Password">
                </div>
                <button class="login-btn" id="loginBtn">Sign In</button>
            </div>
        </div>
    `;

    const triggerLogin = () => {
        const u = div.querySelector('#userid').value;
        const p = div.querySelector('#password').value;
        if (u && p) login(u, p);
    };

    div.querySelector('#loginBtn').onclick = triggerLogin;
    div.querySelector('#password').onkeypress = (e) => {
        if (e.key === 'Enter') triggerLogin();
    };



    return div;
}

function Sidebar() {
    const el = document.createElement('div');
    el.className = 'sidebar';

    // --- Logo / Branding Header ---
    const logo = document.createElement('div');
    logo.className = 'sidebar-logo';
    logo.innerHTML = `
        <i class="fa-solid fa-camera-retro"></i>
        <div class="sidebar-logo-text">
            <span class="sidebar-logo-brand">Photo</span>
            <span class="sidebar-logo-name">Vault</span>
        </div>`;
    el.appendChild(logo);

    // Helper to add a nav item
    const addItem = (id, icon, label) => {
        const navItem = document.createElement('div');
        navItem.className = `nav-item ${state.view === id ? 'active' : ''}`;
        navItem.innerHTML = `<i class="fa-solid ${icon}"></i>${label}`;
        navItem.onclick = () => {
            state.view = id;
            localStorage.setItem('activeView', id);
            state.photoSearchQuery = '';
            if (id === 'albums') {
                state.currentAlbum = null;
                state.selectedAlbums.clear();
                state.albumSelectionMode = false;
            }
            if (id === 'people') { state.currentPerson = null; state.personPhotos = []; }
            refreshViewData();
            render();
        };
        el.appendChild(navItem);
    };

    const addSectionHeader = (label) => {
        const h = document.createElement('div');
        h.className = 'sidebar-section-header';
        h.textContent = label;
        el.appendChild(h);
    };

    const addDivider = () => {
        const d = document.createElement('div');
        d.className = 'sidebar-divider';
        el.appendChild(d);
    };

    // --- Library section ---
    addItem('photos', 'fa-image', 'Photos');
    addItem('videos', 'fa-video', 'Videos');
    addItem('screenshots', 'fa-mobile-screen', 'Screenshots');
    if (!state.config || state.config.search !== 'NO') addItem('search', 'fa-search', 'Search');

    // --- Collections section ---
    addDivider();
    addSectionHeader('Collections');
    addItem('albums', 'fa-book', 'Albums');
    if (!state.config || state.config.people !== 'NO') addItem('people', 'fa-users', 'People');
    if (!state.config || state.config.discover !== 'NO') addItem('discover', 'fa-compass', 'Discover');
    addItem('shared', 'fa-share-nodes', 'Shared Links'); // Renamed Shared with Me to Shared Links

    addDivider();
    addSectionHeader('Manage');
    addItem('files', 'fa-folder-tree', 'File Explorer');
    addItem('upload', 'fa-cloud-arrow-up', 'Upload Media');
    addItem('dashboard', 'fa-table-columns', 'Dashboard');

    // --- User Card + Storage + Logout at bottom ---
    const bottomSection = document.createElement('div');
    bottomSection.className = 'sidebar-user-card';

    if (state.user) {
        const email = state.user.userid || '';
        const namePart = email.split('@')[0];
        const initials = namePart.split(/[._-]/).map(w => w[0]?.toUpperCase() || '').join('').slice(0, 2) || email[0]?.toUpperCase() || '?';
        const displayName = namePart.replace(/[._-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const role = state.user.is_admin ? 'Admin' : 'Member';

        const userCard = document.createElement('div');
        userCard.className = 'user-card';
        userCard.innerHTML = `
            <div class="user-avatar">${initials}</div>
            <div class="user-info">
                <div class="user-name">${displayName}</div>
                <div class="user-role">${role}</div>
            </div>`;
        bottomSection.appendChild(userCard);

        // Storage bar — shows dashboard stats if available
        const stats = state.dashboardStats;
        const usedBytes = stats?.storage?.used_bytes ?? null;
        const usedGb = usedBytes != null ? usedBytes / (1024 ** 3) : null;

        const storageWrap = document.createElement('div');
        storageWrap.className = 'storage-bar-wrap';
        const usedText = usedGb != null ? `${usedGb.toFixed(2)} GB used` : 'Calculating storage…';
        const fillPct = usedGb != null ? Math.min(100, (usedGb / 2) * 100) : 0; // assume 2TB / 2000GB limit roughly, or just pass a percentage if we had total. Assuming 2TB for now based on mockup.
        storageWrap.innerHTML = `
            <div class="storage-bar-label">${usedText}</div>
            <div class="storage-bar-track">
                <div class="storage-bar-fill" style="width:${fillPct}%"></div>
            </div>`;
        bottomSection.appendChild(storageWrap);
    }

    const logoutBtn = document.createElement('div');
    logoutBtn.className = 'nav-item logout-btn';
    logoutBtn.innerHTML = `<i class="fa-solid fa-arrow-right-from-bracket"></i>Sign Out`;
    logoutBtn.onclick = logout;
    bottomSection.appendChild(logoutBtn);

    el.appendChild(bottomSection);
    return el;
}

function logout() {
    const modalOverlay = document.createElement('div');
    modalOverlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);backdrop-filter:blur(8px);z-index:9999;display:flex;align-items:center;justify-content:center;animation:fadeIn 0.2s;';

    const modal = document.createElement('div');
    modal.style.cssText = 'background:#1a1a1c;border:1px solid rgba(255,255,255,0.08);border-radius:24px;padding:32px;width:340px;text-align:center;box-shadow:0 16px 40px rgba(0,0,0,0.4);transform:scale(0.95);animation:scaleUp 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;';

    modal.innerHTML = `
        <div style="background:rgba(255,59,48,0.15);width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;">
            <i class="fa-solid fa-arrow-right-from-bracket" style="font-size:28px;color:#ff3b30;"></i>
        </div>
        <h3 style="margin:0 0 10px;font-size:20px;font-weight:600;color:#fff;">Sign Out</h3>
        <p style="margin:0 0 28px;color:rgba(255,255,255,0.6);font-size:15px;">Are you sure you want to sign out of PhotoVault?</p>
        <div style="display:flex;gap:12px;">
            <button id="logoutCancel" style="flex:1;padding:12px;border-radius:12px;border:none;background:rgba(255,255,255,0.1);color:#fff;font-weight:600;font-size:15px;cursor:pointer;transition:background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.15)'" onmouseout="this.style.background='rgba(255,255,255,0.1)'">Cancel</button>
            <button id="logoutConfirm" style="flex:1;padding:12px;border-radius:12px;border:none;background:#ff3b30;color:#fff;font-weight:600;font-size:15px;cursor:pointer;transition:background 0.2s;" onmouseover="this.style.background='#ff1a0d'" onmouseout="this.style.background='#ff3b30'">Sign Out</button>
        </div>
    `;

    modalOverlay.appendChild(modal);
    document.body.appendChild(modalOverlay);

    modal.querySelector('#logoutCancel').onclick = () => modalOverlay.remove();
    modal.querySelector('#logoutConfirm').onclick = async () => {
        modal.querySelector('#logoutConfirm').innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        try {
            await fetch('/api/logout', { method: 'POST' });
            window.location.reload(); // Reload to clear state and show login
        } catch (e) {
            console.error(e);
            window.location.reload();
        }
    };
}

function PeopleGallery() {
    const container = document.createElement('div');

    // If viewing a specific person's photos
    if (state.currentPerson) {
        container.appendChild(PersonDetailView());
        return container;
    }

    const grid = document.createElement('div');
    grid.className = 'people-grid';

    if (state.people.length === 0) {
        grid.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No people identified yet. Add photos first!</div>`;
        container.appendChild(grid);
        return container;
    }

    state.people.forEach(p => {
        const card = document.createElement('div');
        card.className = 'person-card';

        const namePart = p.thumbnail ? (p.thumbnail.split('/').pop()) : '';
        const finalUrl = namePart ? `/resource/thumbnail/${state.user.userid}/${namePart}` : '';

        card.innerHTML = `
            ${finalUrl
                ? `<img class="person-card-img" src="${finalUrl}" loading="lazy">`
                : `<div class="person-card-placeholder"><i class="fa-solid fa-user"></i></div>`
            }
            <div class="person-card-overlay">
                <span class="person-card-name">${p.name}</span>
                <span class="person-card-count">${p.photo_count || 0} photo${(p.photo_count || 0) !== 1 ? 's' : ''}</span>
            </div>
            <button class="person-delete-btn" title="Delete Person"><i class="fa-solid fa-trash"></i></button>
        `;

        // Delete Handler
        const delBtn = card.querySelector('.person-delete-btn');
        delBtn.onclick = (e) => {
            e.preventDefault();
            e.stopPropagation();
            deletePerson(p.id, p.name);
        };

        // Click card to open detail view
        card.onclick = () => {
            state.currentPerson = p;
            fetchPersonPhotos(p.id);
        };

        grid.appendChild(card);
    });

    container.appendChild(grid);
    return container;
}

function PersonDetailView() {
    const container = document.createElement('div');
    container.className = 'person-detail-view';

    // Header
    const header = document.createElement('div');
    header.className = 'person-detail-header';

    const backBtn = document.createElement('button');
    backBtn.className = 'back-btn';
    backBtn.innerHTML = '<i class="fa-solid fa-chevron-left"></i> People';
    backBtn.onclick = () => {
        state.currentPerson = null;
        state.personPhotos = [];
        render();
    };
    header.appendChild(backBtn);

    const nameEl = document.createElement('h2');
    nameEl.className = 'person-detail-name';
    nameEl.contentEditable = 'true';
    nameEl.innerText = state.currentPerson.name;
    nameEl.onblur = () => {
        const newName = nameEl.innerText.trim();
        if (newName && newName !== state.currentPerson.name) {
            updatePersonName(state.currentPerson.id, newName);
        }
    };
    nameEl.onkeypress = (e) => {
        if (e.key === 'Enter') { e.preventDefault(); nameEl.blur(); }
    };
    header.appendChild(nameEl);

    const countBadge = document.createElement('span');
    countBadge.className = 'person-detail-count';
    countBadge.innerText = `${state.personPhotos.length} photo${state.personPhotos.length !== 1 ? 's' : ''}`;
    header.appendChild(countBadge);

    // Delete button in header
    const delBtn = document.createElement('button');
    delBtn.className = 'person-detail-delete';
    delBtn.innerHTML = '<i class="fa-solid fa-trash"></i> Delete Person';
    delBtn.onclick = () => deletePerson(state.currentPerson.id, state.currentPerson.name);
    header.appendChild(delBtn);

    container.appendChild(header);

    // Photo Grid
    const grid = document.createElement('div');
    grid.className = 'photo-grid';

    if (state.personPhotos.length === 0 && !state.loading) {
        grid.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No photos found for this person.</div>`;
    }

    state.personPhotos.forEach(photo => {
        const item = document.createElement('div');
        item.className = 'photo-item';

        // Add Album Button
        const albumBtn = document.createElement('button');
        albumBtn.className = 'album-btn';
        albumBtn.title = 'Add to Album';
        albumBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
        albumBtn.onclick = (e) => {
            e.stopPropagation();
            openAddToAlbumModal(photo.id);
        };
        item.appendChild(albumBtn);


        const img = document.createElement('img');
        img.src = photo.thumbnail_url;
        img.loading = 'lazy';
        img.onload = () => img.classList.add('loaded');
        item.appendChild(img);

        item.onclick = () => {
            state.viewerList = state.personPhotos;
            state.viewerIndex = state.personPhotos.findIndex(p => p.id === photo.id);
            state.viewerImage = { src: photo.image_url, type: photo.type };
            render();
        };

        grid.appendChild(item);
    });

    container.appendChild(grid);
    return container;
}

function SearchInterface() {
    const container = document.createElement('div');
    container.className = 'search-interface';

    // 1. Controls
    const controls = document.createElement('div');
    controls.className = 'search-controls';

    // Text Search Bar (Large Pill)
    const searchBox = document.createElement('div');
    searchBox.className = 'search-box-large';
    searchBox.innerHTML = `
        <i class="fa-solid fa-magnifying-glass search-icon-large"></i>
        <input type="text" id="searchInput" placeholder="Search your photos by description, location, or objects..." value="${state.searchQuery}">
        <button id="doSearchBtn" class="search-btn-large">Search</button>
    `;
    controls.appendChild(searchBox);

    // People Select (Horizontal scroll)
    if (state.people && state.people.length > 0) {
        const peopleSelect = document.createElement('div');
        peopleSelect.className = 'search-people-select';
        peopleSelect.innerHTML = `<h4 class="search-section-title">Filter by People</h4>`;

        const peopleList = document.createElement('div');
        peopleList.className = 'people-chips people-chips-scrollable';

        // Sort: Named people first, Unknowns later
        const sortedPeople = [...state.people].sort((a, b) => {
            if (a.name === 'Unknown' && b.name !== 'Unknown') return 1;
            if (a.name !== 'Unknown' && b.name === 'Unknown') return -1;
            return a.name.localeCompare(b.name);
        });

        sortedPeople.forEach(p => {
            const chip = document.createElement('div');
            // Give 'Unknown' a slightly different style if needed, or just normal chip
            chip.className = `person-chip ${state.searchPersonIds.includes(p.id) ? 'selected' : ''} ${p.name === 'Unknown' ? 'unknown-chip' : ''}`;

            // Add an avatar circle for the chip
            const initial = p.name === 'Unknown' ? '?' : p.name.charAt(0).toUpperCase();
            chip.innerHTML = `<div class="person-chip-avatar">${initial}</div><span>${p.name}</span>`;

            chip.onclick = () => {
                if (state.searchPersonIds.includes(p.id)) {
                    state.searchPersonIds = state.searchPersonIds.filter(id => id !== p.id);
                } else {
                    state.searchPersonIds.push(p.id);
                }
                render(); // Re-render to update selection style
            };
            peopleList.appendChild(chip);
        });
        peopleSelect.appendChild(peopleList);
        controls.appendChild(peopleSelect);
    }

    container.appendChild(controls);

    // Bind Search Action
    setTimeout(() => {
        const btn = container.querySelector('#doSearchBtn');
        const inp = container.querySelector('#searchInput');
        if (btn && inp) {
            btn.onclick = () => {
                state.searchQuery = inp.value;
                runSearch();
            };
            inp.onkeypress = (e) => {
                if (e.key === 'Enter') {
                    state.searchQuery = inp.value;
                    runSearch();
                }
            };
            // Retain focus
            if (state.searchQuery) {
                inp.focus();
                inp.setSelectionRange(inp.value.length, inp.value.length);
            }
        }
    }, 0);

    // 2. Results
    const resultsGrid = document.createElement('div');
    resultsGrid.className = 'photo-grid';
    if (state.searchResults.length > 0) {
        state.searchResults.forEach(r => {
            const item = document.createElement('div');
            item.className = 'photo-item';

            // Add Album Button
            const albumBtn = document.createElement('button');
            albumBtn.className = 'album-btn';
            albumBtn.title = 'Add to Album';
            albumBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
            albumBtn.onclick = (e) => {
                e.stopPropagation();
                openAddToAlbumModal(r.id);
            };
            item.appendChild(albumBtn);


            const img = document.createElement('img');
            img.src = r.thumbnail_url;
            img.loading = "lazy";
            img.onload = () => img.classList.add('loaded');
            item.appendChild(img);

            item.onclick = () => {
                state.viewerList = state.searchResults;
                state.viewerIndex = state.searchResults.findIndex(p => p.id === r.id);
                state.viewerImage = { src: r.image_url, type: r.type };
                render();
            };

            resultsGrid.appendChild(item);
        });
    } else if (state.searchQuery || state.searchPersonIds.length > 0) {
        resultsGrid.innerHTML = `<div style="padding:20px;">No results found.</div>`;
    }

    container.appendChild(resultsGrid);
    return container;
}

function FileExplorer() {
    const container = document.createElement('div');
    container.className = 'file-explorer';

    // 1. Header with Breadcrumbs / Actions
    const header = document.createElement('div');
    header.className = 'fe-header';

    // Breadcrumbs
    const parts = state.currentPath.split('/').filter(p => p);

    const breadcrumbs = document.createElement('div');
    breadcrumbs.className = 'fe-breadcrumbs';

    if (parts.length > 0) {
        const backBtn = document.createElement('button');
        backBtn.className = 'fe-back-btn';
        backBtn.innerHTML = '<i class="fa-solid fa-chevron-left"></i>';
        backBtn.onclick = () => {
            parts.pop();
            // If popping drops us to completely empty, fallback to the preferred root
            fetchExplorer(parts.length > 0 ? parts.join('/') : 'web/files');
        };
        breadcrumbs.appendChild(backBtn);
    }

    const title = document.createElement('span');
    title.className = 'fe-title';
    title.innerText = parts.length > 0 ? parts[parts.length - 1] : 'Home';
    breadcrumbs.appendChild(title);
    header.appendChild(breadcrumbs);

    // Toolbar (View Toggle + Selection)
    const toolbar = document.createElement('div');
    toolbar.className = 'fe-toolbar';

    if (state.selectedFiles.size > 0) {
        const downloadBtn = document.createElement('button');
        downloadBtn.className = 'fe-tool-btn';
        downloadBtn.innerHTML = `<i class="fa-solid fa-download"></i> Download (${state.selectedFiles.size})`;
        downloadBtn.onclick = downloadSelectedFiles;
        toolbar.appendChild(downloadBtn);

        const delBtn = document.createElement('button');
        delBtn.className = 'fe-tool-btn danger';
        delBtn.innerHTML = `<i class="fa-solid fa-trash"></i> Delete (${state.selectedFiles.size})`;
        delBtn.onclick = batchDeleteFiles;
        toolbar.appendChild(delBtn);

        const clearBtn = document.createElement('button');
        clearBtn.className = 'fe-tool-btn';
        clearBtn.innerText = 'Clear';
        clearBtn.onclick = () => {
            state.selectedFiles.clear();
            render();
        };
        toolbar.appendChild(clearBtn);
    }
    // View Toggle
    const viewToggle = document.createElement('button');
    viewToggle.className = 'fe-tool-btn';
    viewToggle.innerHTML = state.fileViewMode === 'list'
        ? '<i class="fa-solid fa-grip"></i> Grid'
        : '<i class="fa-solid fa-list"></i> List';
    viewToggle.onclick = () => {
        state.fileViewMode = state.fileViewMode === 'list' ? 'grid' : 'list';
        render();
    };
    toolbar.appendChild(viewToggle);

    // Sort Dropdown
    const sortSelect = document.createElement('select');
    sortSelect.className = 'fe-sort-select';
    sortSelect.innerHTML = `
        <option value="name-asc" ${state.sortBy === 'name' && state.sortOrder === 'asc' ? 'selected' : ''}>Name (A-Z)</option>
        <option value="name-desc" ${state.sortBy === 'name' && state.sortOrder === 'desc' ? 'selected' : ''}>Name (Z-A)</option>
        <option value="date-desc" ${state.sortBy === 'date' && state.sortOrder === 'desc' ? 'selected' : ''}>Date (Newest)</option>
        <option value="date-asc" ${state.sortBy === 'date' && state.sortOrder === 'asc' ? 'selected' : ''}>Date (Oldest)</option>
        <option value="size-desc" ${state.sortBy === 'size' && state.sortOrder === 'desc' ? 'selected' : ''}>Size (Largest)</option>
        <option value="size-asc" ${state.sortBy === 'size' && state.sortOrder === 'asc' ? 'selected' : ''}>Size (Smallest)</option>
    `;
    sortSelect.onchange = (e) => {
        const [sortBy, sortOrder] = e.target.value.split('-');
        state.sortBy = sortBy;
        state.sortOrder = sortOrder;
        localStorage.setItem('sortBy', sortBy);
        localStorage.setItem('sortOrder', sortOrder);
        render();
    };
    toolbar.appendChild(sortSelect);

    header.appendChild(toolbar);
    container.appendChild(header);

    // 2. Items List/Grid
    if (state.explorerItems.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'fe-empty';
        empty.innerText = 'This folder is empty';
        container.appendChild(empty);
        return container;
    }

    // Sort items
    const sortedItems = [...state.explorerItems];

    // Separate directories and files
    const directories = sortedItems.filter(item => item.type === 'dir');
    const files = sortedItems.filter(item => item.type !== 'dir');

    // Sort function
    const sortItems = (items) => {
        return items.sort((a, b) => {
            let comparison = 0;

            if (state.sortBy === 'name') {
                const nameA = a.name || '';
                const nameB = b.name || '';
                comparison = nameA.localeCompare(nameB);
            } else if (state.sortBy === 'date') {
                const modA = (a.modified !== undefined && a.modified !== null) ? a.modified : 0;
                const modB = (b.modified !== undefined && b.modified !== null) ? b.modified : 0;
                comparison = modA - modB;
            } else if (state.sortBy === 'size') {
                const sizeA = (a.size !== undefined && a.size !== null) ? a.size : 0;
                const sizeB = (b.size !== undefined && b.size !== null) ? b.size : 0;
                comparison = sizeA - sizeB;
            }

            return state.sortOrder === 'asc' ? comparison : -comparison;
        });
    };

    // Sort directories and files separately
    sortItems(directories);
    sortItems(files);

    // Combine: directories first, then files
    const finalItems = [...directories, ...files];
    const paginatedItems = finalItems.slice(0, state.fileExplorerLimit);

    const itemsContainer = document.createElement('div');
    itemsContainer.className = state.fileViewMode === 'list' ? 'fe-list' : 'fe-grid';

    paginatedItems.forEach(item => {
        const isDir = item.type === 'dir';
        // Determine icon/thumb
        // For grid, if it's an image, try to show thumb? 
        // We can reuse Photo logic for thumbs if it's an image file
        const isImage = !isDir && (item.name.toLowerCase().endsWith('.jpg') || item.name.toLowerCase().endsWith('.png') || item.name.toLowerCase().endsWith('.jpeg'));

        const el = document.createElement('div');
        el.className = state.fileViewMode === 'list' ? 'fe-row' : 'fe-card';
        if (state.selectedFiles.has(item.name)) el.classList.add('selected');

        // Selection Checkbox Logic
        const toggleSelection = (e) => {
            e.stopPropagation();
            if (state.selectedFiles.has(item.name)) state.selectedFiles.delete(item.name);
            else state.selectedFiles.add(item.name);
            render();
        };

        // Content Generation
        if (state.fileViewMode === 'list') {
            const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
            const iconColor = isDir ? '#0a84ff' : '#a1a1a6';

            el.innerHTML = `
                    <div class="fe-checkbox ${state.selectedFiles.has(item.name) ? 'checked' : ''}"><i class="fa-solid fa-check"></i></div>
                    <div class="fe-icon"><i class="fa-solid ${iconClass}" style="color: ${iconColor}"></i></div>
                    <div class="fe-name">${item.name}</div>
                    <div class="fe-size">${isDir ? '' : formatSize(item.size)}</div>
                `;
            el.querySelector('.fe-checkbox').onclick = toggleSelection;

        } else {
            // GRID VIEW
            const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
            const iconColor = isDir ? '#0a84ff' : '#a1a1a6';

            let contentHTML = '';

            if (!isDir && (isImage || item.name.toLowerCase().endsWith('.mp4') || item.name.toLowerCase().endsWith('.mov') || item.name.toLowerCase().endsWith('.avi'))) {
                const fullRelPath = state.currentPath ? `${state.currentPath}/${item.name}` : item.name;
                const parts = fullRelPath.split('/');

                if (parts.length >= 2 && parts[1] === 'files') {
                    const device = parts[0];
                    if (parts.length > 2) {
                        const relPath = parts.slice(2).join('/');
                        const safeRelPath = relPath.replace(/\//g, '_').replace(/\\/g, '_');
                        let thumbName = `${device}__${safeRelPath}`;

                        if (!thumbName.toLowerCase().endsWith('.jpg')) {
                            thumbName += '.jpg';
                        }
                        if (safeRelPath.toLowerCase().endsWith('.jpg') || safeRelPath.toLowerCase().endsWith('.jpeg')) {
                            thumbName = `${device}__${safeRelPath}`;
                            if (safeRelPath.toLowerCase().endsWith('.jpeg')) thumbName += '.jpg';
                        } else {
                            thumbName = `${device}__${safeRelPath}.jpg`;
                        }

                        const thumbSrc = `/resource/thumbnail/${state.user.userid}/${thumbName}`;
                        contentHTML = `<img src="${thumbSrc}" loading="lazy" onload="this.classList.add('loaded')" class="grid-thumb" style="width:100%; height:100%; object-fit: cover; border-radius: 6px;">`;

                        if (!isImage) {
                            contentHTML += '<div class="play-icon-overlay" style="width:30px;height:30px;font-size:16px;"><i class="fa-solid fa-play"></i></div>';
                        }
                    }
                }
            }

            if (!contentHTML) {
                const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
                const iconColor = isDir ? '#0a84ff' : '#a1a1a6';
                contentHTML = `<div class="grid-icon"><i class="fa-solid ${iconClass}" style="color: ${iconColor}; font-size: 48px;"></i></div>`;
            }

            el.innerHTML = `
                <div class="fe-checkbox ${state.selectedFiles.has(item.name) ? 'checked' : ''}"><i class="fa-solid fa-check"></i></div>
                ${contentHTML}
                <div class="grid-name">${item.name}</div>
            `;

            const checkbox = el.querySelector('.fe-checkbox');
            checkbox.onclick = toggleSelection;
        }

        // Content Generation
        if (state.fileViewMode === 'list') {
            const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
            const iconColor = isDir ? '#0a84ff' : '#a1a1a6';

            el.innerHTML = `
                    <div class="fe-checkbox ${state.selectedFiles.has(item.name) ? 'checked' : ''}"><i class="fa-solid fa-check"></i></div>
                    <div class="fe-icon"><i class="fa-solid ${iconClass}" style="color: ${iconColor}"></i></div>
                    <div class="fe-name">${item.name}</div>
                    <div class="fe-size">${isDir ? '' : formatSize(item.size)}</div>
                `;
            el.querySelector('.fe-checkbox').onclick = toggleSelection;

        } else {
            // GRID VIEW
            const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
            const iconColor = isDir ? '#0a84ff' : '#a1a1a6';

            let contentHTML = '';

            if (!isDir && (isImage || item.name.toLowerCase().endsWith('.mp4') || item.name.toLowerCase().endsWith('.mov') || item.name.toLowerCase().endsWith('.avi'))) {
                const fullRelPath = state.currentPath ? `${state.currentPath}/${item.name}` : item.name;
                const parts = fullRelPath.split('/');

                if (parts.length >= 2 && parts[1] === 'files') {
                    const device = parts[0];
                    if (parts.length > 2) {
                        const relPath = parts.slice(2).join('/');
                        const safeRelPath = relPath.replace(/\//g, '_').replace(/\\/g, '_');
                        let thumbName = `${device}__${safeRelPath}`;

                        if (!thumbName.toLowerCase().endsWith('.jpg')) {
                            thumbName += '.jpg';
                        }
                        if (safeRelPath.toLowerCase().endsWith('.jpg') || safeRelPath.toLowerCase().endsWith('.jpeg')) {
                            thumbName = `${device}__${safeRelPath}`;
                            if (safeRelPath.toLowerCase().endsWith('.jpeg')) thumbName += '.jpg';
                        } else {
                            thumbName = `${device}__${safeRelPath}.jpg`;
                        }

                        const thumbSrc = `/resource/thumbnail/${state.user.userid}/${thumbName}`;
                        contentHTML = `<div class="fe-card-icon"><img src="${thumbSrc}" loading="lazy" onload="this.classList.add('loaded')" class="grid-thumb" style="width:100%; height:100%; object-fit: cover;"></div>`;

                        if (!isImage) {
                            contentHTML = `<div class="fe-card-icon"><img src="${thumbSrc}" loading="lazy" onload="this.classList.add('loaded')" class="grid-thumb" style="width:100%; height:100%; object-fit: cover;"><div class="play-icon-overlay" style="width:30px;height:30px;font-size:16px;"><i class="fa-solid fa-play"></i></div></div>`;
                        }
                    }
                }
            }

            if (!contentHTML) {
                const iconClass = isDir ? 'fa-folder' : (isImage ? 'fa-image' : 'fa-file');
                const iconColor = isDir ? '#0a84ff' : '#a1a1a6';
                contentHTML = `<div class="fe-card-icon"><i class="fa-solid ${iconClass}" style="color: ${iconColor}; font-size: 56px;"></i></div>`;
            }

            el.innerHTML = `
                <div class="fe-checkbox-overlay">
                    <div class="fe-checkbox ${state.selectedFiles.has(item.name) ? 'checked' : ''}"><i class="fa-solid fa-check"></i></div>
                </div>
                ${contentHTML}
                <div class="fe-card-name" title="${item.name}">${item.name}</div>
            `;

            const checkbox = el.querySelector('.fe-checkbox');
            checkbox.onclick = toggleSelection;
        }

        // Click Handler: Navigation or Preview
        el.onclick = (e) => {
            if (e.target.closest('.fe-checkbox')) return;

            // Multi-select with Ctrl not implemented, assuming checkbox use

            if (isDir) {
                const newPath = state.currentPath ? `${state.currentPath}/${item.name}` : item.name;
                state.selectedFiles.clear(); // Clear selection on nav
                fetchExplorer(newPath);
            } else {
                const parsed = parseImagePath(state.currentPath, item.name);
                if (parsed) {
                    // Save scroll position
                    const contentArea = document.querySelector('.content-area');
                    if (contentArea) state.savedScrollPosition = contentArea.scrollTop;

                    // Build viewer list from current folder
                    const validItems = state.explorerItems.filter(i => i.type !== 'dir').map(i => {
                        const p = parseImagePath(state.currentPath, i.name);
                        if (!p) return null;
                        const lowerName = i.name.toLowerCase();
                        const isVideo = lowerName.match(/\.(mp4|mov|avi|mkv|webm|mts|m2ts)$/);
                        const isImage = lowerName.match(/\.(jpg|jpeg|png|gif|webp|bmp|svg)$/);

                        if (!isVideo && !isImage) return null;

                        return {
                            src: p.url,
                            type: isVideo ? 'video' : 'image',
                            path: p.path,
                            name: i.name
                        };
                    }).filter(i => i !== null);

                    state.viewerList = validItems;
                    state.viewerIndex = validItems.findIndex(i => i.name === item.name);

                    // Detect if video
                    const isVideo = item.name.toLowerCase().match(/\.(mp4|mov|avi|mkv|webm|mts|m2ts)$/);
                    state.viewerImage = {
                        src: parsed.url,
                        type: isVideo ? 'video' : 'image',
                        path: parsed.path
                    };
                    render();
                }
            }
        };

        itemsContainer.appendChild(el);
    });

    container.appendChild(itemsContainer);

    // Load More Button
    if (finalItems.length > state.fileExplorerLimit) {
        const loadMoreDiv = document.createElement('div');
        loadMoreDiv.style.textAlign = 'center';
        loadMoreDiv.style.padding = '20px';

        const loadBtn = document.createElement('button');
        loadBtn.className = 'btn';
        loadBtn.innerText = `Load More (${finalItems.length - state.fileExplorerLimit} remaining)`;
        loadBtn.onclick = () => {
            const contentArea = document.querySelector('.content-area');
            if (contentArea) {
                state.savedScrollPosition = contentArea.scrollTop;
            }
            state.fileExplorerLimit += 100;
            render();
        };

        loadMoreDiv.appendChild(loadBtn);
        container.appendChild(loadMoreDiv);
    }

    return container;
}

function formatSize(bytes) {
    if (bytes === undefined || bytes === null || isNaN(bytes)) return '0 B';
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(k));
    if (i < 0) return '0 B';
    if (i >= sizes.length) return '> 1 PB';
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Extensions that require server-side transcoding (must match BROWSER_INCOMPATIBLE_VIDEO_EXTS in server.py)
const BROWSER_INCOMPATIBLE_VIDEO_EXTS = ['mts', 'm2ts', 'avi', 'mkv'];

// Helper to determine if a file explorer path matches the server's image serving structure
function parseImagePath(dirPath, filename) {
    // Server serves: /resource/image/<userid>/<device>/<filename>  (or /resource/video/ for incompatible formats)
    // Where valid internal path is: user_dir/<device>/files/<filename>

    // Check if dirPath starts with "something/files"
    const parts = dirPath.split('/');
    if (parts.length >= 2 && parts[1] === 'files') {
        const device = parts[0];
        const relativeSubdir = parts.slice(2).join('/');
        const finalPath = relativeSubdir ? `${relativeSubdir}/${filename}` : filename;

        const ext = filename.split('.').pop().toLowerCase();
        const routePrefix = BROWSER_INCOMPATIBLE_VIDEO_EXTS.includes(ext)
            ? 'video'
            : 'image';

        const url = `/resource/${routePrefix}/${state.user.userid}/${device}/${finalPath}`;
        const realPath = `${device}/files/${finalPath}`;

        return { url, path: realPath };
    }
    return null;
}

// --- Timeline View (for Photos tab) ---

function TimelineView() {
    const container = document.createElement('div');
    container.className = 'timeline-view';

    // Search bar
    const searchBar = document.createElement('div');
    searchBar.className = 'photo-search-bar';
    searchBar.innerHTML = `
        <i class="fa-solid fa-magnifying-glass photo-search-icon"></i>
        <input type="text" class="photo-search-input" placeholder='Search your photos' value="${state.photoSearchQuery}">
        ${state.photoSearchQuery ? '<button class="photo-search-clear"><i class="fa-solid fa-xmark"></i></button>' : ''}
    `;
    const searchInput = searchBar.querySelector('.photo-search-input');
    let searchTimeout;
    searchInput.addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            state.photoSearchQuery = e.target.value.trim();
            refreshViewData();
        }, 400);
    });
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            clearTimeout(searchTimeout);
            state.photoSearchQuery = searchInput.value.trim();
            refreshViewData();
        }
    });
    const clearBtn = searchBar.querySelector('.photo-search-clear');
    if (clearBtn) {
        clearBtn.onclick = () => {
            state.photoSearchQuery = '';
            refreshViewData();
        };
    }
    container.appendChild(searchBar);

    // Retain focus on search input after render
    requestAnimationFrame(() => {
        const input = document.querySelector('.photo-search-input');
        if (input && state.photoSearchQuery) {
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }
    });

    if (state.timelineGroups.length === 0 && !state.loading) {
        const msg = document.createElement('div');
        msg.style.cssText = 'padding:40px 20px; color:var(--text-secondary); text-align:center; font-size:15px;';
        msg.textContent = state.photoSearchQuery ? `No photos matching "${state.photoSearchQuery}"` : 'No photos found.';
        container.appendChild(msg);
        return container;
    }

    state.timelineGroups.forEach(group => {
        const section = document.createElement('div');
        section.className = 'timeline-section';

        // Date header
        const header = document.createElement('div');
        header.className = 'timeline-date-header';

        let formattedDate = 'Unknown Date';
        if (group.date !== 'Unknown') {
            const dateObj = new Date(group.date);
            formattedDate = dateObj.toLocaleDateString('en-US', {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric'
            });
        }

        header.innerHTML = `
            <div>
                <h3>${formattedDate}</h3>
                <span>${group.count} photos</span>
            </div>
            <button class="header-action-btn" title="Add all to Album">
                <i class="fa-solid fa-plus-square"></i>
            </button>
        `;

        // Bind header action
        const actionBtn = header.querySelector('.header-action-btn');
        actionBtn.onclick = (e) => {
            e.stopPropagation();
            const allIds = group.photos.map(p => p.id);
            openAddToAlbumModal(allIds);
        };

        section.appendChild(header);

        // Photo grid for this date
        const grid = document.createElement('div');
        grid.className = 'timeline-grid';

        group.photos.forEach(photo => {
            const item = document.createElement('div');
            item.className = 'photo-item' + (state.selectedPhotos.has(photo.id) ? ' selected' : '');

            // Hover overlay: gradient top/bottom + selection check + date label
            const overlay = document.createElement('div');
            overlay.className = 'photo-item-overlay';

            const selectCheck = document.createElement('div');
            selectCheck.className = 'photo-select-check';
            selectCheck.innerHTML = state.selectedPhotos.has(photo.id) ? '<i class="fa-solid fa-check"></i>' : '';
            overlay.appendChild(selectCheck);

            const dateLabel = document.createElement('div');
            dateLabel.className = 'photo-item-date';
            if (photo.date_taken) {
                const d = new Date(photo.date_taken);
                dateLabel.textContent = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            }
            overlay.appendChild(dateLabel);
            item.appendChild(overlay);

            // Add Album Button (hidden until hover, handled by existing CSS)
            const albumBtn = document.createElement('button');
            albumBtn.className = 'album-btn';
            albumBtn.title = 'Add to Album';
            albumBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
            albumBtn.onclick = (e) => {
                e.stopPropagation();
                openAddToAlbumModal(photo.id);
            };
            item.appendChild(albumBtn);


            if (photo.type === 'video') {
                const playIcon = document.createElement('div');
                playIcon.className = 'play-icon-overlay';
                playIcon.innerHTML = '<i class="fa-solid fa-play"></i>';
                item.appendChild(playIcon);
            }

            const img = document.createElement('img');
            img.src = photo.thumbnail_url;
            img.loading = "lazy";
            img.onload = () => img.classList.add('loaded');
            item.appendChild(img);

            // Tag the element so updateSelectionUI can find it
            item.dataset.photoId = photo.id;

            // Long-press to enter selection mode (mobile)
            let pressTimer = null;
            item.addEventListener('pointerdown', () => {
                pressTimer = setTimeout(() => {
                    pressTimer = null;
                    state.selectionMode = true;
                    togglePhotoSelection(photo.id);
                    render(); // Full render only on first entry (shows toolbar + overlays)
                }, 500);
            });
            item.addEventListener('pointerup', () => { if (pressTimer) clearTimeout(pressTimer); });
            item.addEventListener('pointermove', () => { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } });

            // Selection check click — no full render, just update DOM in-place
            selectCheck.addEventListener('click', (e) => {
                e.stopPropagation();
                state.selectionMode = true;
                togglePhotoSelection(photo.id);
                updateSelectionUI(photo.id);
            });

            item.onclick = () => {
                if (state.selectionMode) {
                    togglePhotoSelection(photo.id);
                    updateSelectionUI(photo.id);
                    return;
                }
                // Save scroll position before opening viewer
                const contentArea = document.querySelector('.content-area');
                if (contentArea) {
                    state.savedScrollPosition = contentArea.scrollTop;
                }
                // Flatten timeline groups for viewer list
                const allPhotos = state.timelineGroups.flatMap(g => g.photos);
                state.viewerList = allPhotos;
                state.viewerIndex = allPhotos.findIndex(p => p.id === photo.id);
                state.viewerImage = { src: photo.image_url, type: photo.type, ...photo };
                render();
            };


            grid.appendChild(item);
        });


        section.appendChild(grid);
        container.appendChild(section);
    });

    return container;
}

// --- Discover View ---

function DiscoverView() {
    const container = document.createElement('div');
    container.className = 'discover-view';

    if (state.memories.length === 0) {
        container.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No memories found. Check back when you have photos from past years!</div>`;
        return container;
    }

    state.memories.forEach(memory => {
        const card = document.createElement('div');
        card.className = 'memory-card';

        // Header
        const header = document.createElement('div');
        header.className = 'memory-header';
        header.innerHTML = `
            <h2>${memory.title}</h2>
            <p>${memory.description}</p>
        `;
        card.appendChild(header);

        // Photo preview grid
        const preview = document.createElement('div');
        preview.className = 'memory-preview';

        // Show up to 6 photos in preview
        const previewPhotos = memory.photos.slice(0, 6);
        previewPhotos.forEach(photo => {
            const item = document.createElement('div');
            item.className = 'photo-item';

            // Add Album Button
            const albumBtn = document.createElement('button');
            albumBtn.className = 'album-btn';
            albumBtn.title = 'Add to Album';
            albumBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
            albumBtn.onclick = (e) => {
                e.stopPropagation();
                openAddToAlbumModal(photo.id);
            };
            item.appendChild(albumBtn);


            const img = document.createElement('img');
            img.src = photo.thumbnail_url;
            img.loading = "lazy";
            img.onload = () => img.classList.add('loaded');
            item.appendChild(img);

            item.onclick = () => {
                state.viewerList = memory.photos;
                state.viewerIndex = memory.photos.findIndex(p => p.id === photo.id);
                state.viewerImage = { src: photo.image_url, type: photo.type };
                render();
            };

            preview.appendChild(item);
        });

        card.appendChild(preview);
        container.appendChild(card);
    });

    return container;
}

// --- Albums View ---

function AlbumsView() {
    const container = document.createElement('div');
    container.className = 'albums-view';

    // If viewing specific album, show album detail
    if (state.currentAlbum) {
        return AlbumDetailView();
    }

    // Header with create button
    const header = document.createElement('div');
    header.className = 'albums-header';
    header.innerHTML = `
        <button class="create-album-btn" id="createAlbumBtn">
            <i class="fa-solid fa-plus"></i> Create Album
        </button>
    `;
    container.appendChild(header);

    // Add Album Selection Toolbar
    if (state.albumSelectionMode || state.selectedAlbums.size > 0) {
        container.appendChild(AlbumSelectionToolbar());
    }

    setTimeout(() => {
        const btn = document.getElementById('createAlbumBtn');
        if (btn) {
            btn.onclick = async () => {
                const name = prompt('Album name:');
                if (name) {
                    await createAlbum(name);
                }
            };
        }
    }, 0);

    // Albums grid
    const grid = document.createElement('div');
    grid.className = 'albums-grid';

    const albumsToShow = state.albums;

    if (albumsToShow.length === 0) {
        grid.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No albums yet. Create one to get started!</div>`;
    } else {
        albumsToShow.forEach(album => {
            const card = document.createElement('div');
            card.className = 'album-card';

            // Check if selected
            const isSelected = state.selectedAlbums.has(album.id);
            if (isSelected) card.classList.add('selected');

            // Select Checkbox button
            const selectBtn = document.createElement('div');
            selectBtn.className = 'photo-select-check album-select-check'; // reuse photo select styles
            selectBtn.innerHTML = isSelected ? '<i class="fa-solid fa-check"></i>' : '';
            if (isSelected) selectBtn.style.opacity = '1';

            selectBtn.onclick = (e) => {
                e.stopPropagation();
                if (state.selectedAlbums.has(album.id)) {
                    state.selectedAlbums.delete(album.id);
                } else {
                    state.selectedAlbums.add(album.id);
                }
                if (state.selectedAlbums.size === 0) {
                    state.albumSelectionMode = false;
                } else {
                    state.albumSelectionMode = true;
                }
                document.body.classList.toggle('album-selection-active', state.albumSelectionMode);
                render();
            };
            card.appendChild(selectBtn);

            // Cover photo
            const cover = document.createElement('div');
            cover.className = 'album-cover';
            if (album.cover_url) {
                cover.style.backgroundImage = `url(${album.cover_url})`;
            } else {
                cover.innerHTML = `<i class="fa-solid fa-images"></i>`;
            }
            card.appendChild(cover);

            // Album info
            const info = document.createElement('div');
            info.className = 'album-info';
            info.innerHTML = `
                <h3>${album.name}</h3>
                <p>${album.photo_count} photos</p>
            `;
            if (album.album_type === 'auto_date') {
                const badge = document.createElement('span');
                badge.className = 'album-badge';
                badge.textContent = 'Auto';
                info.appendChild(badge);
            } else if (album.album_type === 'shared') {
                const badge = document.createElement('span');
                badge.className = 'album-badge';
                badge.textContent = 'Shared';
                badge.style.color = '#34c759';
                badge.style.background = 'rgba(52, 199, 89, 0.2)';
                info.appendChild(badge);
            }
            card.appendChild(info);

            // Delete button
            const delBtn = document.createElement('button');
            delBtn.className = 'album-delete-btn';
            delBtn.innerHTML = `<i class="fa-solid fa-trash"></i>`;
            delBtn.onclick = (e) => {
                e.stopPropagation();
                deleteAlbum(album.id);
            };
            card.appendChild(delBtn);

            // Share button (for single albums directly)
            if (album.album_type === 'manual' || album.album_type === 'auto_date') {
                const shareBtn = document.createElement('button');
                shareBtn.className = 'album-delete-btn'; // reuse styling
                shareBtn.style.right = '44px';
                shareBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i>';
                shareBtn.title = 'Create Share Link';
                shareBtn.onclick = (e) => {
                    e.stopPropagation();
                    openShareLinkModal(album.id, 'album', album.name);
                };
                card.appendChild(shareBtn);
            }

            // Click to view album (or select if in selection mode)
            card.onclick = () => {
                if (state.albumSelectionMode) {
                    selectBtn.click();
                } else {
                    fetchAlbumPhotos(album.id);
                }
            };

            grid.appendChild(card);
        });
    }

    container.appendChild(grid);
    return container;
}

function clearAlbumSelection() {
    state.selectedAlbums.clear();
    state.albumSelectionMode = false;
    document.body.classList.remove('album-selection-active');
    render();
}

function AlbumSelectionToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'selection-toolbar visible';

    const count = state.selectedAlbums.size;
    const countEl = document.createElement('span');
    countEl.className = 'selection-toolbar-count';
    countEl.textContent = `${count} album${count !== 1 ? 's' : ''} selected`;
    toolbar.appendChild(countEl);

    const divider = document.createElement('div');
    divider.className = 'selection-toolbar-divider';
    toolbar.appendChild(divider);

    // Check if any selected album is a shared album
    const selectedAlbumObjects = Array.from(state.selectedAlbums).map(id => state.albums.find(a => a.id === id));
    const hasSharedAlbum = selectedAlbumObjects.some(a => a && a.album_type === 'shared');

    // Share Albums (Only if no shared albums are selected)
    if (!hasSharedAlbum) {
        const shareBtn = document.createElement('button');
        shareBtn.className = 'sel-btn sel-btn-album';
        shareBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i> Share';
        shareBtn.onclick = () => {
            const ids = Array.from(state.selectedAlbums).join(',');
            const title = `${count} Album${count !== 1 ? 's' : ''}`;
            openShareLinkModal(ids, 'album', title);
            clearAlbumSelection();
        };
        toolbar.appendChild(shareBtn);
    }

    // Delete Albums
    const delBulkBtn = document.createElement('button');
    delBulkBtn.className = 'sel-btn';
    delBulkBtn.style.background = 'rgba(255, 59, 48, 0.2)';
    delBulkBtn.style.color = '#ff453a';
    delBulkBtn.innerHTML = '<i class="fa-solid fa-trash"></i> Delete';
    delBulkBtn.onclick = () => {
        if (confirm(`Are you sure you want to delete ${count} selected album(s)?`)) {
            const ids = Array.from(state.selectedAlbums);
            ids.forEach(id => {
                deleteAlbum(id, true); // Assuming bulk delete won't re-render heavily or needs silent support
            });
            setTimeout(() => {
                clearAlbumSelection();
                refreshViewData();
            }, 500);
        }
    };
    toolbar.appendChild(delBulkBtn);

    // Cancel selection
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'sel-btn sel-btn-cancel';
    cancelBtn.innerHTML = '<i class="fa-solid fa-xmark"></i> Cancel';
    cancelBtn.onclick = clearAlbumSelection;
    toolbar.appendChild(cancelBtn);

    return toolbar;
}

function openShareLinkModal(assetId, assetType, title) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.width = '450px';

    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Share ${title || 'this item'}</div>
            <button class="modal-close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body" style="text-align: left;">
            <div style="margin-bottom: 20px;">
                <label style="font-size:13px; font-weight:600; color:var(--text-secondary); margin-bottom:8px; display:block;">Share with Users</label>
                <div id="usersList" style="max-height: 150px; overflow-y: auto; background: rgba(0,0,0,0.2); border-radius: 8px; padding: 10px; border: 1px solid rgba(255,255,255,0.05); margin-bottom: 10px;">
                    <div style="color:var(--text-secondary); font-size:13px; text-align:center;">Loading users...</div>
                </div>
                <button id="shareSelectedBtn" class="sel-btn" style="width:100%; display:none; justify-content:center; background:var(--accent-color); color:white; border:none; padding:10px; border-radius:8px;">Share to Selected Users</button>
            </div>
            
            <hr style="border: none; border-top: 1px solid rgba(255,255,255,0.1); margin: 20px 0;">

            <div style="margin-bottom: 20px;">
                <label style="font-size:13px; font-weight:600; color:var(--text-secondary); margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                    Share with Link
                    <button id="toggleLinkOptionsBtn" style="background:none; border:none; color:var(--accent-color); cursor:pointer; font-size:12px;">Create Public Link</button>
                </label>
                
                <div id="linkOptions" style="display:none; margin-top:15px;">
                    <p style="font-size:12px; color:var(--text-secondary); margin-bottom:15px;">Anyone with this link can view this item.</p>
                    
                    <div class="input-group" style="margin-bottom:10px;">
                        <input type="text" id="shareLinkName" placeholder="Link Name (Required)" maxlength="80" style="width:100%; padding:10px; border-radius:8px; border:1px solid var(--accent-color); background:rgba(0,0,0,0.2); color:#fff; font-size:13px;">
                    </div>
                    
                    <div class="input-group" style="margin-bottom:10px;">
                        <input type="password" id="sharePassword" placeholder="Password (Required)" style="width:100%; padding:10px; border-radius:8px; border:1px solid rgba(255,255,255,0.1); background:rgba(0,0,0,0.2); color:#fff; font-size:13px;">
                    </div>
                    
                    <div class="input-group" style="margin-bottom:15px;">
                        <input type="number" id="shareExpiry" placeholder="Expiration in Days (Optional)" min="1" style="width:100%; padding:10px; border-radius:8px; border:1px solid rgba(255,255,255,0.1); background:rgba(0,0,0,0.2); color:#fff; font-size:13px;">
                    </div>
                    
                    <button class="share-submit-btn" id="generateLinkBtn" style="width:100%; padding:10px; border-radius:8px; background:var(--overlay-light-10); color:white; border:none; cursor:pointer;">Generate Link</button>
                </div>

                <div id="shareLinkResult" style="display:none; margin-top:15px;">
                    <div style="display:flex; gap:8px;">
                        <input type="text" id="shareUrl" readonly style="flex:1; padding:10px; border-radius:8px; border:1px solid var(--accent-color); background:rgba(0,0,0,0.2); color:#fff; font-size:12px;">
                        <button id="copyShareUrlBtn" style="padding:10px 15px; border-radius:8px; background:var(--accent-color); color:#fff; border:none; cursor:pointer;"><i class="fa-regular fa-copy"></i></button>
                    </div>
                </div>
            </div>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };

    // Fetch and render users
    const renderUsers = async () => {
        try {
            const res = await fetch('/api/users');
            const data = await res.json();
            const usersListEl = modal.querySelector('#usersList');
            const shareSelectedBtn = modal.querySelector('#shareSelectedBtn');

            if (data.users && data.users.length > 0) {
                usersListEl.innerHTML = '';
                data.users.forEach(email => {
                    const label = document.createElement('label');
                    label.style.cssText = 'display:flex; align-items:center; gap:10px; margin-bottom:8px; cursor:pointer; font-size:13px; color:#fff;';
                    label.innerHTML = `
                        <input type="checkbox" value="${email}" class="user-share-cb">
                        <span>${email}</span>
                    `;
                    usersListEl.appendChild(label);
                });

                // Show button when checkboxes are clicked
                usersListEl.addEventListener('change', () => {
                    const anyChecked = modal.querySelectorAll('.user-share-cb:checked').length > 0;
                    shareSelectedBtn.style.display = anyChecked ? 'flex' : 'none';
                });
            } else {
                usersListEl.innerHTML = '<div style="color:var(--text-secondary); font-size:13px; text-align:center;">No other users available.</div>';
            }
        } catch (e) {
            modal.querySelector('#usersList').innerHTML = '<div style="color:#ff453a; font-size:13px; text-align:center;">Failed to load users.</div>';
        }
    };
    renderUsers();

    // Handle Sharing to Selected Users
    modal.querySelector('#shareSelectedBtn').onclick = async () => {
        const checkboxes = modal.querySelectorAll('.user-share-cb:checked');
        const emails = Array.from(checkboxes).map(cb => cb.value);
        if (emails.length === 0) return;

        const btn = modal.querySelector('#shareSelectedBtn');
        const originalText = btn.textContent;
        btn.textContent = 'Sharing...';
        btn.disabled = true;

        try {
            // Note: Currently only implemented for albums in the backend based on requirements, but extensible
            const endpoint = assetType === 'album'
                ? `/api/albums/${assetId}/share/user`
                : '/api/share'; // fallback or unsupported for now

            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shared_with_emails: emails, asset_id: assetId, asset_type: assetType })
            });
            const data = await res.json();

            if (res.ok) {
                btn.textContent = 'Shared Successfully!';
                btn.style.background = '#34c759'; // Success green
                setTimeout(() => {
                    close();
                }, 1500);
            } else {
                alert(data.error || 'Share failed');
                btn.textContent = originalText;
                btn.disabled = false;
            }
        } catch (e) {
            console.error(e);
            alert('Share error');
            btn.textContent = originalText;
            btn.disabled = false;
        }
    };

    // Toggle Link Options
    modal.querySelector('#toggleLinkOptionsBtn').onclick = () => {
        const pnl = modal.querySelector('#linkOptions');
        pnl.style.display = pnl.style.display === 'none' ? 'block' : 'none';
    };

    modal.querySelector('#copyShareUrlBtn').onclick = () => {
        const urlInput = modal.querySelector('#shareUrl');
        urlInput.select();
        document.execCommand('copy');
        const btn = modal.querySelector('#copyShareUrlBtn');
        btn.innerHTML = '<i class="fa-solid fa-check"></i>';
        setTimeout(() => { btn.innerHTML = '<i class="fa-regular fa-copy"></i>'; }, 2000);
    };

    modal.querySelector('#generateLinkBtn').onclick = async () => {
        const linkName = modal.querySelector('#shareLinkName').value.trim();
        const pwd = modal.querySelector('#sharePassword').value;
        const exp = modal.querySelector('#shareExpiry').value;
        const btn = modal.querySelector('#generateLinkBtn');

        if (!linkName) {
            const nameInput = modal.querySelector('#shareLinkName');
            nameInput.style.borderColor = '#ff453a';
            nameInput.placeholder = 'Link Name is required!';
            nameInput.focus();
            return;
        }

        if (!pwd) {
            const pwdInput = modal.querySelector('#sharePassword');
            pwdInput.style.borderColor = '#ff453a';
            pwdInput.placeholder = 'Password is required!';
            pwdInput.focus();
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Generating...';

        try {
            const res = await fetch('/api/links/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    asset_id: assetId,
                    asset_type: assetType,
                    link_name: linkName,
                    password: pwd || undefined,
                    expiry_days: exp ? parseInt(exp) : undefined
                })
            });
            const data = await res.json();
            if (res.ok && data.link_hash) {
                const url = window.location.origin + '/s/' + data.link_hash;
                modal.querySelector('#shareUrl').value = url;
                modal.querySelector('#shareLinkResult').style.display = 'block';
                btn.style.display = 'none';
            } else {
                alert(data.error || 'Failed to create link');
                btn.disabled = false;
                btn.textContent = 'Generate Link';
            }
        } catch (e) {
            alert('Error creating link');
            btn.disabled = false;
            btn.textContent = 'Generate Link';
        }
    };
}

function AlbumDetailView() {
    const container = document.createElement('div');
    container.className = 'album-detail-view';

    // Back button and header
    const header = document.createElement('div');
    header.className = 'album-detail-header';
    header.innerHTML = `
        <button class="back-btn" id="backToAlbumsBtn">
            <i class="fa-solid fa-arrow-left"></i> Back to Albums
        </button>
        <h2>Album Photos</h2>
    `;
    container.appendChild(header);

    setTimeout(() => {
        const btn = document.getElementById('backToAlbumsBtn');
        if (btn) {
            btn.onclick = () => {
                state.currentAlbum = null;
                render();
            };
        }
    }, 0);

    // Photo grid
    const grid = document.createElement('div');
    grid.className = 'photo-grid';

    if (!state.currentAlbum.photos || state.currentAlbum.photos.length === 0) {
        grid.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No photos in this album.</div>`;
    } else {
        state.currentAlbum.photos.forEach(photo => {
            const item = document.createElement('div');
            item.className = 'photo-item';

            // Only show action buttons for owned photos (not received/shared photos)
            // Also hide for all photos in shared albums
            const isSharedAlbum = state.currentAlbum.album_type === 'shared';
            if (!photo.is_received && !isSharedAlbum) {
                // Add Album Button
                const albumBtn = document.createElement('button');
                albumBtn.className = 'album-btn';
                albumBtn.title = 'Add to Album';
                albumBtn.innerHTML = '<i class="fa-solid fa-plus"></i>';
                albumBtn.onclick = (e) => {
                    e.stopPropagation();
                    openAddToAlbumModal(photo.id); /**/
                };
                item.appendChild(albumBtn);
            }
            if (!isSharedAlbum) {

            }

            if (photo.type === 'video') {
                const playIcon = document.createElement('div');
                playIcon.className = 'play-icon-overlay';
                playIcon.innerHTML = '<i class="fa-solid fa-play"></i>';
                item.appendChild(playIcon);
            }

            const img = document.createElement('img');
            img.src = photo.thumbnail_url;
            img.loading = "lazy";
            img.onload = () => img.classList.add('loaded');
            item.appendChild(img);

            item.onclick = () => {
                // Save scroll position
                const contentArea = document.querySelector('.content-area');
                if (contentArea) state.savedScrollPosition = contentArea.scrollTop;

                state.viewerList = state.currentAlbum.photos;
                state.viewerIndex = state.currentAlbum.photos.findIndex(p => p.id === photo.id);
                state.viewerImage = { src: photo.image_url, type: photo.type };
                render();
            };

            grid.appendChild(item);
        });
    }

    container.appendChild(grid);
    return container;
}

function PhotoGallery() {
    const grid = document.createElement('div');
    grid.className = 'photo-grid';

    // Aggregate files from all devices
    let allFiles = [];
    state.devices.forEach(d => {
        if (d.files) {
            allFiles = allFiles.concat(d.files.map(f => ({ ...f, device: d.name })));
        }
    });

    if (allFiles.length === 0) {
        grid.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No photos found.</div>`;
        return grid;
    }

    allFiles.forEach(f => {
        const item = document.createElement('div');
        item.className = 'photo-item';

        // Thumb extraction (Assumes specific flat format from server)
        // Thumb extraction (Assumes specific flat format from server)
        const safeRelPath = f.rel_path.replace(/\//g, '_').replace(/\\/g, '_');

        let thumbName = `${f.device}__${safeRelPath}`;
        if (!thumbName.toLowerCase().endsWith('.jpg')) {
            thumbName += '.jpg';
        }
        // If it already ends in .jpg (or .jpeg etc but daemon normalizes to .jpg if single ext check?), 
        // Daemon logic: if ends with .jpg, keeps it. if ends with .png, adds .jpg?
        // Daemon: if safe_base.lower().endswith('.jpg'): safe_name = ... else: safe_name = ... + .jpg
        // So daemon ALWAYS ensures it ends in .jpg.
        // But if original was .png, daemon makes it .png.jpg
        // If original was .jpg, daemon makes it .jpg (NOT .jpg.jpg)

        // So in JS:
        // if f.rel_path ends in .jpg, safeRelPath ends in .jpg.
        // We want NO extra .jpg.
        // If f.rel_path ends in .png, safeRelPath ends in .png.
        // Daemon makes it .png.jpg.

        // So logic:
        // If ends in .jpg, leave it.
        // If not, append .jpg.

        if (safeRelPath.toLowerCase().endsWith('.jpg') || safeRelPath.toLowerCase().endsWith('.jpeg')) {
            thumbName = `${f.device}__${safeRelPath}`;
            // Normalize jpeg to jpg if daemon does? Daemon check: `if safe_base.lower().endswith('.jpg')` -> keeps it.
            // If `endswith('.jpeg')` -> goes to else -> adds `.jpg`. So `foo.jpeg.jpg`.

            if (safeRelPath.toLowerCase().endsWith('.jpeg')) thumbName += '.jpg';
        } else {
            thumbName = `${f.device}__${safeRelPath}.jpg`;
        }
        const thumbSrc = `/resource/thumbnail/${state.user.userid}/${thumbName}`;
        const fullSrc = `/resource/image/${state.user.userid}/${f.device}/${f.rel_path}`;

        const img = document.createElement('img');
        img.src = thumbSrc;
        img.loading = "lazy";
        img.onload = () => img.classList.add('loaded');
        item.appendChild(img);

        item.onclick = () => {
            const isVideo = f.rel_path.toLowerCase().match(/\.(mp4|mov|avi|mkv|webm)$/);
            state.viewerImage = { src: fullSrc, type: isVideo ? 'video' : 'image' };
            render();
        };

        grid.appendChild(item);
    });

    return grid;
}

// ... (PhotoGallery remains separate or we can merge?)


// Global metadata state
let currentMetadata = null;

async function fetchMetadata(image) {
    currentMetadata = { loading: true };
    try {
        let url = `/api/photo/metadata`;

        // Prefer ID if available
        if (image.id) {
            url += `?id=${encodeURIComponent(image.id)}`;
        } else if (image.path) {
            // Explicit path provided (e.g. from FileExplorer)
            url += `?path=${encodeURIComponent(image.path)}`;
        } else {
            // Fallback to path extraction if ID not available (e.g. from FileExplorer)
            // image.src is /resource/image/<userid>/<device>/<rel_path>
            // exact logic in parseImagePath: url: `/resource/image/${state.user.userid}/${device}/${finalPath}`

            const prefix = `/resource/image/${state.user.userid}/`;
            if (image.src.startsWith(prefix)) {
                // extracted: <device>/<rel_path>
                const pathPart = image.src.substring(prefix.length);
                // pathPart is raw URL encoded? Chrome handles it, but let's just pass it.
                // It might need decoding if it was encoded in src.
                // Usually src is URL encoded.
                url += `&path=${encodeURIComponent(decodeURIComponent(pathPart))}`;
            }
        }

        const res = await fetch(url);
        const data = await res.json();
        if (data.found) {
            currentMetadata = data;
        } else {
            currentMetadata = { found: false };
        }
    } catch (e) {
        currentMetadata = { error: true };
        console.error("Metadata fetch error", e);
    }
    render();
}

async function fetchSharedPhotos() {
    state.loading = true;
    render();
    try {
        const res = await fetch(`/api/shared-with-me`);
        const data = await res.json();
        if (res.ok && data.shared_assets) {
            state.sharedAssets = data.shared_assets; // New state variable
        }
    } catch (e) {
        console.error('Shared assets fetch error', e);
    } finally {
        state.loading = false;
        render();
    }
}

function SharedView() {
    const container = document.createElement('div');
    container.className = 'shared-view';

    const header = document.createElement('div');
    header.innerHTML = `<h2>Shared with You</h2><p style="color:var(--text-secondary); margin-bottom:20px; font-size:14px;">Albums and photos shared directly with you by other users.</p>`;
    container.appendChild(header);

    const listContainer = document.createElement('div');
    listContainer.className = 'albums-grid'; // Reuse album grid styling

    if (!state.sharedAssets) {
        listContainer.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">Loading shared assets...</div>';
    } else if (state.sharedAssets.length === 0) {
        listContainer.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">No assets have been shared with you yet.</div>';
        listContainer.className = '';
    } else {
        state.sharedAssets.forEach(asset => {
            const card = document.createElement('div');
            card.className = 'album-card';

            const coverHtml = asset.thumbnail_url
                ? `<img src="${asset.thumbnail_url}" class="album-cover" style="object-fit:cover;">`
                : `<div class="album-cover" style="display:flex;align-items:center;justify-content:center;background:var(--overlay-light-5);"><i class="fa-solid fa-users" style="font-size:32px;color:var(--text-secondary);"></i></div>`;

            card.innerHTML = `
                ${coverHtml}
                <div class="album-info" style="padding-bottom: 10px;">
                    <h3>${asset.asset_title || 'Shared ' + asset.asset_type}</h3>
                    <p>Shared by: ${asset.owner_email}</p>
                </div>
            `;

            card.onclick = () => {
                // If it's an album, we can view its contents using the newly modified fetchAlbumPhotos
                if (asset.asset_type === 'album') {
                    fetchAlbumPhotos(asset.asset_id, asset).then(() => {
                        state.view = 'albums';
                        render();
                    });
                } else {
                    alert("Viewing individual shared photos directly from this view is not yet fully implemented. Please view shared albums.");
                }
            };

            listContainer.appendChild(card);
        });
    }

    container.appendChild(listContainer);
    return container;
}

// --- Zoom and Pan Helpers ---

function updateViewerTransform() {
    // After CamanJS init, the img is replaced by a canvas — target all three
    const media = document.querySelector('.lightbox-media img, .lightbox-media canvas, .lightbox-media video');
    const container = document.querySelector('.lightbox-media');

    if (media && container) {
        // We calculate bounds before applying the transform, but need its natural dimensions
        // The scale applies to the center.
        const scale = state.zoomLevel / 100;

        // Remove transform temporarily to measure natural unscaled space
        media.style.transform = 'none';
        const rect = media.getBoundingClientRect();
        const containerRect = container.getBoundingClientRect();

        let naturalWidth = media.naturalWidth || media.videoWidth || rect.width;
        let naturalHeight = media.naturalHeight || media.videoHeight || rect.height;

        let drawnWidth = rect.width;
        let drawnHeight = rect.height;

        if (naturalWidth && naturalHeight) {
            const imgRatio = naturalWidth / naturalHeight;
            const elRatio = rect.width / rect.height;

            if (imgRatio > elRatio) {
                // Image is wider than container, height is letterboxed
                drawnWidth = rect.width;
                drawnHeight = rect.width / imgRatio;
            } else {
                // Image is taller than container, width is pillarboxed
                drawnHeight = rect.height;
                drawnWidth = rect.height * imgRatio;
            }
        }

        // Calculate the scaled dimensions of the actual drawn pixels
        const scaledWidth = drawnWidth * scale;
        const scaledHeight = drawnHeight * scale;

        let maxPanX = 0;
        if (scaledWidth > containerRect.width) {
            maxPanX = (scaledWidth - containerRect.width) / 2;
        }

        let maxPanY = 0;
        if (scaledHeight > containerRect.height) {
            maxPanY = (scaledHeight - containerRect.height) / 2;
        }

        // Clamp the pan coordinates
        state.panX = Math.max(-maxPanX, Math.min(maxPanX, state.panX));
        state.panY = Math.max(-maxPanY, Math.min(maxPanY, state.panY));

        // Re-apply the transform
        media.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${scale})`;
    }
}

function handleWheel(e) {
    if (!state.viewerImage) return;
    e.preventDefault();

    const delta = Math.sign(e.deltaY) * -1;
    const step = 10;
    let newZoom = state.zoomLevel + (delta * step);

    // Clamp zoom
    if (newZoom < 20) newZoom = 20;
    if (newZoom > 500) newZoom = 500;

    if (newZoom !== state.zoomLevel) {
        state.zoomLevel = newZoom;
        updateZoomUI(newZoom);
        updateViewerTransform();
    }
}

function handleMouseDown(e) {
    if (!state.viewerImage) return;
    // Only allow drag if zoomed in or if we want to allow panning nicely
    if (e.button !== 0) return; // Only left click

    state.isDragging = true;
    state.dragStartX = e.clientX - state.panX;
    state.dragStartY = e.clientY - state.panY;

    const media = document.querySelector('.lightbox-media img, .lightbox-media video');
    if (media) media.style.cursor = 'grabbing';
}

function handleMouseMove(e) {
    if (!state.isDragging || !state.viewerImage) return;
    e.preventDefault();

    state.panX = e.clientX - state.dragStartX;
    state.panY = e.clientY - state.dragStartY;

    updateViewerTransform();
}

function handleMouseUp() {
    state.isDragging = false;
    const media = document.querySelector('.lightbox-media img, .lightbox-media video');
    if (media) media.style.cursor = 'grab';
}

// --- Pinch-to-zoom & touch pan for mobile lightbox ---

let _pinchStartDist = null;
let _pinchStartZoom = null;
let _touchStartX = null;
let _touchStartY = null;
let _touchStartPanX = null;
let _touchStartPanY = null;

function getTouchDist(touches) {
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
}

function handleLightboxTouchStart(e) {
    if (e.touches.length === 2) {
        e.preventDefault();
        _pinchStartDist = getTouchDist(e.touches);
        _pinchStartZoom = state.zoomLevel;
    } else if (e.touches.length === 1) {
        _touchStartX = e.touches[0].clientX;
        _touchStartY = e.touches[0].clientY;
        _touchStartPanX = state.panX;
        _touchStartPanY = state.panY;
    }
}

function handleLightboxTouchMove(e) {
    if (e.touches.length === 2 && _pinchStartDist !== null) {
        e.preventDefault();
        const dist = getTouchDist(e.touches);
        let newZoom = Math.round(_pinchStartZoom * (dist / _pinchStartDist));
        newZoom = Math.max(20, Math.min(500, newZoom));
        state.zoomLevel = newZoom;
        updateZoomUI(newZoom);
        updateViewerTransform();
    } else if (e.touches.length === 1 && _touchStartX !== null && state.zoomLevel > 100) {
        e.preventDefault();
        state.panX = _touchStartPanX + (e.touches[0].clientX - _touchStartX);
        state.panY = _touchStartPanY + (e.touches[0].clientY - _touchStartY);
        updateViewerTransform();
    }
}

function handleLightboxTouchEnd(e) {
    if (e.touches.length < 2) {
        _pinchStartDist = null;
        _pinchStartZoom = null;
    }
    if (e.touches.length === 0) {
        _touchStartX = null;
        _touchStartY = null;
    }
}

function attachLightboxTouchHandlers(el) {
    el.addEventListener('touchstart', handleLightboxTouchStart, { passive: false });
    el.addEventListener('touchmove', handleLightboxTouchMove, { passive: false });
    el.addEventListener('touchend', handleLightboxTouchEnd, { passive: true });
}

function initCamanEditor(el, img) {
    if (!window.Caman) return;

    // Capture the original src BEFORE Caman replaces the <img> with a <canvas>
    const originalSrc = img.src;
    img.onload = () => {
        const camanInstance = window.Caman(img, function () {
            this.render(); // Initial silent render to convert img -> canvas

            // Caman replaces <img> with a <canvas> at natural pixel dimensions.
            // Re-apply CSS constraints so it fits inside the flex container.
            requestAnimationFrame(() => {
                const canvas = el.querySelector('canvas#caman-image');
                if (canvas) {
                    canvas.style.maxWidth = '100%';
                    canvas.style.maxHeight = '100%';
                    canvas.style.width = 'auto';
                    canvas.style.height = 'auto';
                    canvas.style.objectFit = 'contain';
                    canvas.style.display = 'block';
                    canvas.style.transformOrigin = 'center';
                    // Re-apply current pan/zoom transform
                    const scale = (state.zoomLevel || 100) / 100;
                    canvas.style.transform = `translate(${state.panX || 0}px, ${state.panY || 0}px) scale(${scale})`;
                }
            });

            // — Slider element refs —
            const slExposure = el.querySelector('#slExposure');
            const slContrast = el.querySelector('#slContrast');
            const slHighlights = el.querySelector('#slHighlights');
            const slShadows = el.querySelector('#slShadows');
            const slWhites = el.querySelector('#slWhites');
            const slBlacks = el.querySelector('#slBlacks');

            const valExposure = el.querySelector('#valExposure');
            const valContrast = el.querySelector('#valContrast');
            const valHighlights = el.querySelector('#valHighlights');
            const valShadows = el.querySelector('#valShadows');
            const valWhites = el.querySelector('#valWhites');
            const valBlacks = el.querySelector('#valBlacks');

            // ── LIVE PREVIEW: CSS filter (GPU-accelerated, zero lag) ─────────
            // Maps slider values (-100…100) to CSS filter functions.
            // This does NOT touch pixel data; it's just a visual overlay.
            const applyCSS = () => {
                const media = el.querySelector('#mediaContainer canvas, #mediaContainer img');
                if (!media) return;

                const exp = parseInt(slExposure?.value) || 0;
                const con = parseInt(slContrast?.value) || 0;
                const hi = parseInt(slHighlights?.value) || 0;
                const sha = parseInt(slShadows?.value) || 0;
                const whi = parseInt(slWhites?.value) || 0;
                const bla = parseInt(slBlacks?.value) || 0;

                // brightness: exposure + whites contribution
                const bright = Math.max(0.05, 1 + (exp * 0.9 + whi * 0.4) / 100);
                // contrast
                const cont = Math.max(0.05, 1 + con / 100);
                // highlights → gentle additional brightness on top
                const hiBoost = Math.max(0.8, 1 + hi * 0.004);
                // shadows → lifted blacks via brightness (rough)
                const shaBoost = Math.max(0.8, 1 + sha * 0.003);
                // blacks/vibrance → saturation shift
                const sat = Math.max(0.1, 1 + bla * 0.008);

                media.style.filter = [
                    `brightness(${(bright * hiBoost * shaBoost).toFixed(3)})`,
                    `contrast(${cont.toFixed(3)})`,
                    `saturate(${sat.toFixed(3)})`
                ].join(' ');
            };

            // Bind input events — labels update + CSS preview, no CamanJS
            const sliders = [slExposure, slContrast, slHighlights, slShadows, slWhites, slBlacks];
            const valEls = [valExposure, valContrast, valHighlights, valShadows, valWhites, valBlacks];
            sliders.forEach((sl, i) => {
                if (!sl) return;
                sl.addEventListener('input', () => {
                    if (valEls[i]) valEls[i].textContent = sl.value;
                    applyCSS();
                });
            });

            // ── SAVE via offscreen canvas (no CamanJS, no flicker, no corruption) ──
            // We reload the original full-res image into an invisible canvas,
            // apply ctx.filter (which actually bakes pixels, unlike CSS filter),
            // then toDataURL. Clean, accurate, no race conditions.
            const buildFilterString = () => {
                const exp = parseInt(slExposure?.value) || 0;
                const con = parseInt(slContrast?.value) || 0;
                const hi = parseInt(slHighlights?.value) || 0;
                const sha = parseInt(slShadows?.value) || 0;
                const whi = parseInt(slWhites?.value) || 0;
                const bla = parseInt(slBlacks?.value) || 0;

                const bright = Math.max(0.05, 1 + (exp * 0.9 + whi * 0.4) / 100);
                const cont = Math.max(0.05, 1 + con / 100);
                const hiB = Math.max(0.8, 1 + hi * 0.004);
                const shaB = Math.max(0.8, 1 + sha * 0.003);
                const sat = Math.max(0.1, 1 + bla * 0.008);
                return [
                    `brightness(${(bright * hiB * shaB).toFixed(3)})`,
                    `contrast(${cont.toFixed(3)})`,
                    `saturate(${sat.toFixed(3)})`
                ].join(' ');
            };

            const saveViaOffscreen = async (mode, btn, originalText, newFilename) => {
                return new Promise((resolve) => {
                    const filterStr = buildFilterString();
                    const tempImg = new Image();
                    tempImg.crossOrigin = 'anonymous';
                    tempImg.onload = async () => {
                        // Draw at full natural resolution with filter baked in
                        const offscreen = document.createElement('canvas');
                        offscreen.width = tempImg.naturalWidth;
                        offscreen.height = tempImg.naturalHeight;
                        const ctx = offscreen.getContext('2d');
                        ctx.filter = filterStr;
                        ctx.drawImage(tempImg, 0, 0);

                        const base64Data = offscreen.toDataURL('image/jpeg', 0.95);
                        try {
                            const res = await fetch('/api/files/edit', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    file_id: state.viewerImage.id,
                                    image_data: base64Data,
                                    save_mode: mode,
                                    new_filename: newFilename || undefined
                                })
                            });
                            const data = await res.json();
                            if (res.ok) {
                                alert(mode === 'overwrite'
                                    ? 'Image saved successfully!'
                                    : 'New edited copy saved!');
                                scanFiles();
                                refreshViewData();
                            } else {
                                alert(data.error || 'Failed to save image');
                            }
                        } catch (e) {
                            alert('Network error saving image');
                        } finally {
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                            resolve();
                        }
                    };
                    tempImg.onerror = () => {
                        alert('Could not load original image for saving');
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                        resolve();
                    };
                    // Add cache-bust so browser doesn't serve a cached tainted copy
                    tempImg.src = originalSrc + (originalSrc.includes('?') ? '&' : '?') + '_save=' + Date.now();
                });
            };


            const btnSave = el.querySelector('#btnEditorSave');
            const btnSaveAs = el.querySelector('#btnEditorSaveAs');

            if (btnSave) btnSave.onclick = () => {
                const orig = btnSave.innerHTML;
                btnSave.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving…';
                btnSave.disabled = true;
                saveViaOffscreen('overwrite', btnSave, orig);
            };
            if (btnSaveAs) btnSaveAs.onclick = () => {
                // Derive a sensible default name from the original src
                const srcName = originalSrc.split('/').pop().split('?')[0] || 'photo';
                const dotIdx = srcName.lastIndexOf('.');
                const defaultName = dotIdx > 0
                    ? srcName.slice(0, dotIdx) + '_copy' + srcName.slice(dotIdx)
                    : srcName + '_copy';

                const chosenName = prompt('Save a copy as:', defaultName);
                if (!chosenName || !chosenName.trim()) return; // cancelled

                const orig = btnSaveAs.innerHTML;
                btnSaveAs.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving…';
                btnSaveAs.disabled = true;
                saveViaOffscreen('save_as', btnSaveAs, orig, chosenName.trim());
            };
        });
    };

    // In case image was already loaded from cache before we bound .onload
    if (img.complete) {
        img.onload();
    }
}

function updateZoomUI(val) {
    const zoomValEl = document.getElementById('zoomValue');
    const rangeInput = document.querySelector('.zoom-slider');

    // Update State and Transform
    state.zoomLevel = parseInt(val);
    updateViewerTransform();

    // Update UI Elements
    if (zoomValEl) zoomValEl.textContent = val + '%';
    if (rangeInput && rangeInput.value !== val) rangeInput.value = val;
}

function MediaViewer() {
    if (!state.viewerImage) {
        currentMetadata = null;
        return document.createTextNode('');
    }

    if (!state.viewerImage._metaFetched) {
        state.viewerImage._metaFetched = true;
        // Reset pan/zoom whenever a new image is opened
        state.panX = 0;
        state.panY = 0;
        state.zoomLevel = 100;
        fetchMetadata(state.viewerImage);
    }

    const el = document.createElement('div');
    el.className = 'lightbox active';

    let mediaContent = '';
    // Ensure we start with transform
    const transformStyle = `transform: translate(${state.panX}px, ${state.panY}px) scale(${(state.zoomLevel || 100) / 100}); transition: transform 0.1s ease`;

    if (state.viewerImage.type === 'video') {
        mediaContent = `<video src="${state.viewerImage.src}" controls autoplay style="max-width:100%; max-height:100%; ${transformStyle}" class="viewer-media"></video>`;
    } else {
        mediaContent = `<img src="${state.viewerImage.src}" style="${transformStyle}" class="viewer-media" />`;
    }

    // --- Build unified right panel ---
    // Metadata section
    let metaSection = '';
    if (currentMetadata && currentMetadata.loading) {
        metaSection = `<div class="lb-panel-loading"><i class="fa-solid fa-circle-notch fa-spin"></i></div>`;
    } else if (currentMetadata && currentMetadata.found) {
        const m = currentMetadata;
        const dateTaken = m.date_taken ? new Date(m.date_taken).toLocaleString() : null;
        const dateUploaded = m.timestamp ? new Date(m.timestamp).toLocaleString() : null;

        metaSection = `
            <div class="lb-panel-filename">
                <i class="fa-regular fa-image lb-panel-filename-icon"></i>
                <span>${m.filename}</span>
            </div>
            <div class="lb-panel-meta-grid">
                ${dateTaken ? `
                <div class="lb-meta-item">
                    <i class="fa-regular fa-calendar lb-meta-icon"></i>
                    <div>
                        <div class="lb-meta-label">Date Taken</div>
                        <div class="lb-meta-value">${dateTaken}</div>
                    </div>
                </div>` : ''}
                ${dateUploaded ? `
                <div class="lb-meta-item">
                    <i class="fa-solid fa-cloud-arrow-up lb-meta-icon"></i>
                    <div>
                        <div class="lb-meta-label">Uploaded</div>
                        <div class="lb-meta-value">${dateUploaded}</div>
                    </div>
                </div>` : ''}
                ${m.size ? `
                <div class="lb-meta-item">
                    <i class="fa-solid fa-weight-hanging lb-meta-icon"></i>
                    <div>
                        <div class="lb-meta-label">File Size</div>
                        <div class="lb-meta-value">${formatSize(m.size)}</div>
                    </div>
                </div>` : ''}
                ${(m.location_lat && m.location_lon) ? `
                <div class="lb-meta-item">
                    <i class="fa-solid fa-location-dot lb-meta-icon"></i>
                    <div>
                        <div class="lb-meta-label">GPS</div>
                        <div class="lb-meta-value">
                            <a href="https://www.google.com/maps/search/?api=1&query=${m.location_lat},${m.location_lon}" target="_blank" style="color:var(--accent-color); text-decoration:none;">${m.location_lat.toFixed(4)}, ${m.location_lon.toFixed(4)}</a>
                        </div>
                    </div>
                </div>` : ''}
                ${m.description ? `
                <div class="lb-meta-item lb-meta-item-full">
                    <i class="fa-solid fa-tag lb-meta-icon"></i>
                    <div>
                        <div class="lb-meta-label">Tags</div>
                        <div class="lb-meta-value">${m.description}</div>
                    </div>
                </div>` : ''}
            </div>

            <!-- Zoom Control -->
            <div class="lb-panel-section-label"><i class="fa-solid fa-magnifying-glass"></i> Zoom</div>
            <div class="lb-zoom-row">
                <i class="fa-solid fa-magnifying-glass-minus lb-zoom-icon"></i>
                <input type="range" min="20" max="500" value="${state.zoomLevel || 100}" class="zoom-slider lb-zoom-slider" oninput="updateZoomUI(this.value)">
                <i class="fa-solid fa-magnifying-glass-plus lb-zoom-icon"></i>
                <span id="zoomValue" class="lb-zoom-value">${state.zoomLevel || 100}%</span>
            </div>
        `;
    } else {
        metaSection = `<div class="lb-panel-loading" style="font-size:13px;">No metadata available</div>`;
    }

    // Editor section (photos only)
    let editorSection = '';
    if (state.viewerImage.type !== 'video') {
        editorSection = `
            <div class="lb-panel-divider"></div>
            <div class="lb-panel-section-label"><i class="fa-solid fa-wand-magic-sparkles"></i> Adjust</div>

            <div class="lb-editor-controls" id="lbEditorControls">
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-solid fa-sun"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Exposure</span>
                            <span class="lb-editor-val" id="valExposure">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slExposure" min="-100" max="100" value="0">
                    </div>
                </div>
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-solid fa-circle-half-stroke"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Contrast</span>
                            <span class="lb-editor-val" id="valContrast">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slContrast" min="-100" max="100" value="0">
                    </div>
                </div>
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-regular fa-sun"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Highlights</span>
                            <span class="lb-editor-val" id="valHighlights">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slHighlights" min="-100" max="100" value="0">
                    </div>
                </div>
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-solid fa-moon"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Shadows</span>
                            <span class="lb-editor-val" id="valShadows">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slShadows" min="-100" max="100" value="0">
                    </div>
                </div>
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-solid fa-circle"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Whites</span>
                            <span class="lb-editor-val" id="valWhites">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slWhites" min="-100" max="100" value="0">
                    </div>
                </div>
                <div class="lb-editor-row">
                    <div class="lb-editor-icon"><i class="fa-regular fa-circle"></i></div>
                    <div class="lb-editor-control">
                        <div class="lb-editor-label-row">
                            <span class="lb-editor-label">Blacks</span>
                            <span class="lb-editor-val" id="valBlacks">0</span>
                        </div>
                        <input type="range" class="editor-slider lb-editor-slider" id="slBlacks" min="-100" max="100" value="0">
                    </div>
                </div>
            </div>

            <div class="lb-editor-actions">
                <button class="editor-btn editor-btn-save" id="btnEditorSave"><i class="fa-solid fa-floppy-disk"></i> Save</button>
                <button class="editor-btn editor-btn-saveas" id="btnEditorSaveAs"><i class="fa-regular fa-copy"></i> Save As Copy</button>
            </div>
        `;
    }

    // Navigation Arrows
    let navControls = '';
    if (state.viewerList.length > 1) {
        navControls = `
            <button class="nav-arrow left" id="navLeft"><i class="fa-solid fa-chevron-left"></i></button>
            <button class="nav-arrow right" id="navRight"><i class="fa-solid fa-chevron-right"></i></button>
        `;
    }

    el.innerHTML = `
        <button class="lightbox-mobile-btn lightbox-btn-close" id="lbCloseBtn" title="Close"><i class="fa-solid fa-xmark"></i></button>
        <button class="lightbox-mobile-btn lightbox-btn-info" id="lbInfoBtn" title="Info"><i class="fa-solid fa-circle-info"></i></button>
        <div class="lightbox-content">
            <div class="lightbox-media" id="mediaContainer">
                 ${mediaContent}
                 ${navControls}
            </div>
            <div class="lb-unified-panel" id="lbSidebar" onclick="event.stopPropagation()">
                ${metaSection}
                ${editorSection}
            </div>
        </div>
        <div class="close-help hide-on-mobile">Press ESC to close</div>
    `;

    // Event Listeners for Interaction
    // Use requestAnimationFrame so flex layout has settled before we measure dims
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            const container = el.querySelector('#mediaContainer');
            if (container) {
                container.addEventListener('wheel', handleWheel, { passive: false });
                container.addEventListener('mousedown', handleMouseDown);
                container.addEventListener('mousemove', handleMouseMove);
                container.addEventListener('mouseup', handleMouseUp);
                container.addEventListener('mouseleave', handleMouseUp); // Stop drag if leaves

                // Prevent default drag behavior of images
                const img = container.querySelector('img');
                if (img) {
                    img.id = 'caman-image'; // Assign ID for Caman bindings
                    img.crossOrigin = "Anonymous"; // Ensure cross-origin is set for canvas
                    img.ondragstart = (e) => e.preventDefault();
                    img.style.cursor = 'grab';

                    // Initialize CamanJS Binding Logic
                    initCamanEditor(el, img);
                }
                const video = container.querySelector('video');
                if (video) {
                    video.style.cursor = 'grab';
                }

                // Navigation Listeners
                const leftBtn = el.querySelector('#navLeft');
                const rightBtn = el.querySelector('#navRight');

                if (leftBtn) {
                    leftBtn.onclick = (e) => {
                        e.stopPropagation();
                        navigateViewer(-1);
                    };
                }
                if (rightBtn) {
                    rightBtn.onclick = (e) => {
                        e.stopPropagation();
                        navigateViewer(1);
                    };
                }

                // Mobile Buttons Listeners
                const closeBtn = el.querySelector('#lbCloseBtn');
                if (closeBtn) {
                    closeBtn.onclick = (e) => {
                        e.stopPropagation();
                        state.viewerImage = null;
                        state.viewerList = [];
                        state.viewerIndex = -1;
                        currentMetadata = null;
                        state.zoomLevel = 100;
                        state.panX = 0;
                        state.panY = 0;
                        render();
                    };
                }
                const infoBtn = el.querySelector('#lbInfoBtn');
                if (infoBtn) {
                    infoBtn.onclick = (e) => {
                        e.stopPropagation();
                        const sidebar = el.querySelector('#lbSidebar');
                        if (sidebar) sidebar.classList.toggle('active-mobile');
                    };
                }
            }
        }); // inner rAF
    }); // outer rAF

    el.onclick = (e) => {
        // Close if clicking outside the content area (on the background)
        // Check if we were dragging? If so, don't close.
        // We can use a small threshold or check isDragging.
        if (state.isDragging) return;

        if (e.target === el || e.target.classList.contains('lightbox-content') || e.target.classList.contains('lightbox-media')) {
            state.viewerImage = null;
            state.viewerList = [];
            state.viewerIndex = -1;
            currentMetadata = null;
            state.zoomLevel = 100;
            state.panX = 0;
            state.panY = 0;
            render();
        }
    };

    // Attach touch handlers for pinch-to-zoom and touch pan on mobile
    attachLightboxTouchHandlers(el);

    return el;
}
function navigateViewer(direction) {
    if (!state.viewerList || state.viewerList.length <= 1) return;

    let newIndex = state.viewerIndex + direction;

    // Bounds check (looping could be an option, but let's stick to bounds for now)
    if (newIndex < 0) return; // or newIndex = state.viewerList.length - 1; (loop)
    if (newIndex >= state.viewerList.length) return; // or newIndex = 0; (loop)

    state.viewerIndex = newIndex;
    const nextItem = state.viewerList[newIndex];

    // Normalize item to viewerImage format
    const src = nextItem.src || nextItem.image_url;

    state.viewerImage = {
        src: src,
        type: nextItem.type,
        ...nextItem
    };

    // Reset Zoom/Pan
    state.zoomLevel = 100;
    state.panX = 0;
    state.panY = 0;
    currentMetadata = null; // Clear old metadata

    render();
}

function Dashboard() {
    const container = document.createElement('div');
    container.className = 'dashboard-container';

    if (!state.dashboardStats) {
        if (!state.loadingStats) {
            state.loadingStats = true;
            fetchDashboardStats().finally(() => { state.loadingStats = false; });
        }
        container.innerHTML = '<div class="loading">Loading stats...</div>';
        return container;
    }

    const s = state.dashboardStats;
    const sys = s.system || {};
    const media = s.media || {};
    const storage = s.storage || {};
    const ai = s.ai || {};

    // Header
    const header = document.createElement('div');
    header.className = 'dashboard-header';
    header.innerHTML = `
        <h1>Welcome Back, ${state.user.userid}</h1>
        <p class="subtitle">System Overview & Statistics</p>
    `;
    container.appendChild(header);

    // Grid
    const grid = document.createElement('div');
    grid.className = 'dashboard-grid';

    // 1. Storage Card (Wide)
    const storageCard = document.createElement('div');
    storageCard.className = 'dash-card wide';
    const usedGB = ((storage.used_bytes || 0) / (1024 * 1024 * 1024)).toFixed(2);
    const totalGB = sys.disk_total ? (sys.disk_total / (1024 * 1024 * 1024)).toFixed(0) : '?';
    const percent = sys.disk_percent || 0;

    storageCard.innerHTML = `
        <div class="card-header"><i class="fa-solid fa-hard-drive"></i> Storage Usage</div>
        <div class="storage-visual">
            <div class="storage-info">
                <span class="big-stat">${usedGB} GB</span>
                <span class="sub-stat">of ${totalGB} GB used</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar" style="width: ${percent}%"></div>
            </div>
            <div class="storage-details">
                <span>Free: ${sys.disk_free ? (sys.disk_free / (1024 * 1024 * 1024)).toFixed(2) : '?'} GB</span>
                <span>${storage.file_count ? storage.file_count.toLocaleString() : 0} Files</span>
            </div>
        </div>
    `;
    grid.appendChild(storageCard);

    // 2. Media Counts
    const mediaCard = document.createElement('div');
    mediaCard.className = 'dash-card';
    mediaCard.innerHTML = `
        <div class="card-header"><i class="fa-solid fa-photo-film"></i> Library</div>
        <div class="stat-grid">
            <div class="stat-item">
                <i class="fa-solid fa-image" style="color:#4facfe"></i>
                <div class="stat-value">${media.photos || 0}</div>
                <div class="stat-label">Photos</div>
            </div>
            <div class="stat-item">
                <i class="fa-solid fa-video" style="color:#f093fb"></i>
                <div class="stat-value">${media.videos || 0}</div>
                <div class="stat-label">Videos</div>
            </div>
            <div class="stat-item">
                <i class="fa-solid fa-mobile-screen" style="color:#ffecd2"></i>
                <div class="stat-value">${media.screenshots || 0}</div>
                <div class="stat-label">Screenshots</div>
            </div>
            <div class="stat-item">
                <i class="fa-solid fa-book" style="color:#a18cd1"></i>
                <div class="stat-value">${media.albums || 0}</div>
                <div class="stat-label">Albums</div>
            </div>
        </div>
    `;
    grid.appendChild(mediaCard);

    // 3. AI Processing
    const aiCard = document.createElement('div');
    aiCard.className = 'dash-card';
    aiCard.innerHTML = `
        <div class="card-header"><i class="fa-solid fa-wand-magic-sparkles"></i> AI Intelligence</div>
        <div class="ai-stats">
            <div class="ai-row">
                <span>People Identified</span>
                <span class="badge">${ai.people_count || 0}</span>
            </div>
            <div class="ai-row">
                <span>Faces Processed</span>
                <span class="badge secondary">${ai.processed_faces || 0}</span>
            </div>
             <div class="ai-row">
                <span>Descriptions Generated</span>
                <span class="badge secondary">${ai.processed_desc || 0}</span>
            </div>
        </div>
    `;
    grid.appendChild(aiCard);

    // 4. System Health
    const sysCard = document.createElement('div');
    sysCard.className = 'dash-card';
    const cpu = sys.cpu_percent || 0;
    const mem = sys.memory_percent || 0;

    // Uptime formatter
    const uptimeSec = sys.uptime_seconds || 0;
    const d = Math.floor(uptimeSec / (3600 * 24));
    const h = Math.floor(uptimeSec % (3600 * 24) / 3600);
    const m = Math.floor(uptimeSec % 3600 / 60);
    const uptimeStr = `${d}d ${h}h ${m}m`;

    sysCard.innerHTML = `
        <div class="card-header"><i class="fa-solid fa-server"></i> System Health</div>
        <div class="sys-stats">
             <div class="sys-row">
                <span>CPU Load</span>
                <div class="mini-bar-container"><div class="mini-bar" style="width:${cpu}%"></div></div>
                <span>${cpu}%</span>
            </div>
            <div class="sys-row">
                <span>Memory</span>
                <div class="mini-bar-container"><div class="mini-bar" style="width:${mem}%"></div></div>
                <span>${mem}%</span>
            </div>
            <div class="sys-info-text">
                OS: ${sys.platform || 'Linux'} <br>
                Uptime: ${uptimeStr}
            </div>
        </div>
    `;
    grid.appendChild(sysCard);



    container.appendChild(grid);

    // 5. Configuration Settings
    const configSection = document.createElement('div');
    configSection.className = 'dashboard-settings-section';
    configSection.style.cssText = 'margin-top: 30px; background: var(--surface-2); border-radius: 16px; padding: 24px; border: 1px solid var(--border-color);';

    const cfg = state.config || { port: 8877, ai: "YES", search: "YES", people: "YES", discover: "YES" };

    configSection.innerHTML = `
        <style>
        .toggle-switch { position: relative; display: inline-block; width: 44px; height: 24px; }
        .toggle-switch input { opacity: 0; width: 0; height: 0; }
        .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255,255,255,0.1); transition: .2s; border-radius: 24px; border: 1px solid var(--border-color); }
        .toggle-slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 3px; bottom: 3px; background-color: var(--text-secondary); transition: .2s; border-radius: 50%; }
        .toggle-switch input:checked + .toggle-slider { background-color: var(--accent-color); border-color: var(--accent-color); }
        .toggle-switch input:checked + .toggle-slider:before { transform: translateX(20px); background-color: #fff; }
        .setting-item { display:flex; justify-content:space-between; align-items:center;  padding: 12px 0; border-bottom: 1px solid var(--border-color); }
        .setting-item:last-child { border-bottom: none; }
        .setting-label { font-size: 0.95rem; color: var(--text-primary); }
        .setting-desc { font-size: 0.8rem; color: var(--text-secondary); margin-top: 4px; }
        </style>
        <h2 style="margin-bottom: 20px; font-size: 1.25rem;"><i class="fa-solid fa-sliders"></i> Application Configuration</h2>
        <p style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 20px;">Changes made here will be saved to config.json. Please reboot the server for UI layout and process changes to take effect.</p>
        
        <div style="display: flex; flex-direction: column;">
            <div class="setting-item">
                <div>
                    <div class="setting-label">Server Port</div>
                    <div class="setting-desc">Port used by the PhotoVault web server.</div>
                </div>
                <input type="number" id="cfg-port" value="${cfg.port}" style="width: 80px; padding: 6px 10px; border-radius: 6px; background: rgba(0,0,0,0.2); border: 1px solid var(--border-color); color: #fff; text-align: right;">
            </div>
            <div class="setting-item">
                <div>
                    <div class="setting-label">Enable AI Processing</div>
                    <div class="setting-desc">Toggles automated facial recognition and photo description generation.</div>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="cfg-ai" ${cfg.ai !== 'NO' ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>
            <div class="setting-item">
                <div>
                    <div class="setting-label">Show Search Tab</div>
                    <div class="setting-desc">Enables or disables the AI-powered search tool in the sidebar.</div>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="cfg-search" ${cfg.search !== 'NO' ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>
            <div class="setting-item">
                <div>
                    <div class="setting-label">Show People Tab</div>
                    <div class="setting-desc">Enables or disables the facial recognition gallery.</div>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="cfg-people" ${cfg.people !== 'NO' ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>
            <div class="setting-item">
                <div>
                    <div class="setting-label">Show Discover Tab</div>
                    <div class="setting-desc">Enables or disables the memories and discovery feed.</div>
                </div>
                <label class="toggle-switch">
                    <input type="checkbox" id="cfg-discover" ${cfg.discover !== 'NO' ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                </label>
            </div>
        </div>
        <div style="margin-top: 24px; display: flex; justify-content: flex-end;">
            <button id="save-config-btn" class="upload-btn"><i class="fa-solid fa-floppy-disk"></i> Save Settings</button>
        </div>
    `;

    configSection.querySelector('#save-config-btn').onclick = async () => {
        const newCfg = {
            port: parseInt(configSection.querySelector('#cfg-port').value) || 8877,
            ai: configSection.querySelector('#cfg-ai').checked ? "YES" : "NO",
            search: configSection.querySelector('#cfg-search').checked ? "YES" : "NO",
            people: configSection.querySelector('#cfg-people').checked ? "YES" : "NO",
            discover: configSection.querySelector('#cfg-discover').checked ? "YES" : "NO"
        };
        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(newCfg)
            });
            if (res.ok) {
                alert('Settings saved securely to config.json! Please reboot your server to apply these changes.');
            } else {
                alert('Failed to save settings to config.json.');
            }
        } catch (e) {
            alert('A network error occurred while attempting to save the configuration.');
        }
    };

    container.appendChild(configSection);

    return container;
}

function SharedView() {
    const container = document.createElement('div');
    container.className = 'shared-view';

    const header = document.createElement('div');
    header.innerHTML = `<h2>Active Shared Links</h2><p style="color:var(--text-secondary); margin-bottom:20px; font-size:14px;">Manage public links you have created for your media.</p>`;
    container.appendChild(header);

    const listContainer = document.createElement('div');
    listContainer.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">Loading shared links...</div>';
    container.appendChild(listContainer);

    // Fetch links
    fetch('/api/links/list')
        .then(r => r.json())
        .then(data => {
            listContainer.innerHTML = '';
            const links = data.links || [];

            if (links.length === 0) {
                listContainer.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">You have no active shared links.</div>';
                return;
            }

            links.forEach(link => {
                const item = document.createElement('div');
                item.style.cssText = 'background:rgba(255,255,255,0.05); padding:15px; border-radius:12px; margin-bottom:15px; display:flex; justify-content:space-between; align-items:center; flex-wrap: wrap; gap: 10px;';

                const url = window.location.origin + '/s/' + link.link_hash;
                const created = new Date(link.created_at).toLocaleDateString();
                const expires = link.expires_at ? new Date(link.expires_at).toLocaleDateString() : 'Never';
                const typeLabel = link.asset_type.charAt(0).toUpperCase() + link.asset_type.slice(1);

                item.innerHTML = `
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:600; margin-bottom:5px; display:flex; align-items:center; gap:8px;">
                            <i class="fa-solid fa-link" style="color:var(--accent-color); flex-shrink:0;"></i>
                            <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${link.link_name || (typeLabel + ' Link')}</span>
                            ${link.is_protected ? '<i class="fa-solid fa-lock" style="font-size:12px; color:#f093fb; flex-shrink:0;" title="Password Protected"></i>' : ''}
                        </div>
                        <div style="font-size:12px; color:var(--text-secondary); margin-bottom:6px;">${typeLabel} &bull; Created: ${created} &bull; Expires: ${expires}</div>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <a href="${url}" target="_blank" style="font-size:12px; color:var(--accent-color); text-decoration:none; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:280px;">${url}</a>
                            <button class="copy-link-btn" title="Copy link" style="padding:4px 10px; border-radius:6px; background:rgba(255,255,255,0.08); color:#fff; border:none; cursor:pointer; font-size:12px; flex-shrink:0;"><i class="fa-regular fa-copy"></i></button>
                        </div>
                    </div>
                    <div style="flex-shrink:0;">
                        <button class="revoke-btn" style="padding:8px 16px; border-radius:8px; background:rgba(255,59,48,0.2); color:#ff3b30; border:none; cursor:pointer; font-weight:600; transition:0.2s;"><i class="fa-solid fa-trash"></i> Revoke</button>
                    </div>
                `;

                item.querySelector('.copy-link-btn').onclick = () => {
                    navigator.clipboard.writeText(url).then(() => {
                        const btn = item.querySelector('.copy-link-btn');
                        btn.innerHTML = '<i class="fa-solid fa-check"></i>';
                        setTimeout(() => { btn.innerHTML = '<i class="fa-regular fa-copy"></i>'; }, 2000);
                    });
                };

                item.querySelector('.revoke-btn').onclick = async () => {
                    if (!confirm('Revoke this link? Anyone with the link will lose access immediately.')) return;
                    try {
                        const res = await fetch('/api/links/revoke', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ link_hash: link.link_hash })
                        });
                        if (res.ok) {
                            item.remove();
                            if (listContainer.children.length === 0) {
                                listContainer.innerHTML = '<div style="padding:20px; color:var(--text-secondary)">You have no active shared links.</div>';
                            }
                        } else {
                            alert('Failed to revoke link');
                        }
                    } catch (e) {
                        alert('Error revoking link');
                    }
                };

                listContainer.appendChild(item);
            });
        })
        .catch(err => {
            listContainer.innerHTML = '<div style="padding:20px; color:#ff3b30">Error loading links.</div>';
        });

    return container;
}

function ContentArea() {
    const el = document.createElement('div');
    el.className = 'content-area';

    // Header
    const header = document.createElement('div');
    header.className = 'content-header';
    header.innerHTML = `<div class="content-title">${state.view.charAt(0).toUpperCase() + state.view.slice(1)}</div>`;

    // Refresh Btn
    const refBtn = document.createElement('button');
    refBtn.className = 'refresh-btn';
    refBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i>';
    refBtn.onclick = () => {
        if (state.view === 'files') fetchExplorer(state.currentPath);
        else if (state.view === 'people') fetchPeople();
        else if (state.view === 'search') runSearch();
        else if (state.view === 'photos') fetchTimeline();
        else if (state.view === 'albums') fetchAlbums();
        else if (state.view === 'discover') fetchMemories();
        else if (state.view === 'videos') fetchTimeline('video');
    };
    header.appendChild(refBtn);
    el.appendChild(header);

    // Body
    if (state.loading) {
        const spin = document.createElement('div');
        spin.className = 'spinner';
        el.appendChild(spin);
        return el;
    }

    if (state.view === 'files') {
        el.appendChild(FileExplorer());
    } else if (state.view === 'dashboard') {
        el.appendChild(Dashboard());
    } else if (state.view === 'photos') {
        el.appendChild(TimelineView());
    } else if (state.view === 'screenshots') {
        el.appendChild(TimelineView());
    } else if (state.view === 'people') {
        el.appendChild(PeopleGallery());
    } else if (state.view === 'search') {
        el.appendChild(SearchInterface());
    } else if (state.view === 'videos') {
        el.appendChild(TimelineView()); // Reuse timeline for videos
    } else if (state.view === 'discover') {
        el.appendChild(DiscoverView());
    } else if (state.view === 'albums') {
        el.appendChild(AlbumsView());
    } else if (state.view === 'shared') {
        el.appendChild(SharedView());
    } else if (state.view === 'upload') {
        el.appendChild(UploadView());
    } else {
        el.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">Coming soon...</div>`;
    }

    return el;
}

function UploadView() {
    const container = document.createElement('div');
    container.className = 'upload-view-content';
    container.style.padding = '24px';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.gap = '24px';
    container.style.height = '100%';
    container.style.maxWidth = '900px';
    container.style.margin = '0 auto';
    container.style.width = '100%';

    // Dropzone Area
    const dropzone = document.createElement('div');
    dropzone.className = 'upload-dropzone';

    const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    dropzone.innerHTML = `
        <i class="fa-solid fa-cloud-arrow-up upload-icon"></i>
        <h3>${isMobile ? 'Tap here to upload media' : 'Click or drag folders here'}</h3>
        <p>${isMobile ? 'Select photos and videos from your gallery' : 'Folders will be automatically scanned for media'}</p>
    `;

    // Hidden file input to allow selecting folders manually via browsing
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    if (!isMobile) {
        fileInput.webkitdirectory = true;
    } else {
        fileInput.accept = 'image/*,video/*';
    }
    fileInput.style.display = 'none';

    fileInput.addEventListener('change', (e) => {
        if (!state.user) return;
        uploadManager.handleFilesInput(e.target.files);
    });

    dropzone.addEventListener('click', () => {
        fileInput.click();
    });

    // Handle drag on the dedicated dropzone
    dropzone.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dropzone.classList.add('drag-active');
    });

    dropzone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-active');
    });

    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-active');

        if (!state.user) return;
        uploadManager.handleDropEvent(e);
    });

    container.appendChild(fileInput);
    container.appendChild(dropzone);

    // Status Area Wrapper
    const statusCard = document.createElement('div');
    statusCard.className = 'upload-status-card';
    statusCard.innerHTML = `
        <div class="upload-status-header">
            <h4>Upload progress</h4>
            <div id="uploadTabHeaderStatus" class="upload-status-subtitle"></div>
        </div>
        
        <div class="upload-list-header">
            <div class="col-icon"></div>
            <div class="col-name">File Name</div>
            <div class="col-status">Status</div>
            <div class="col-progress">Progress</div>
        </div>
        
        <div id="uploadTabTableBody" class="upload-list-body">
            <div class="upload-list-empty">No active uploads</div>
        </div>
    `;
    container.appendChild(statusCard);

    // Tell upload manager to bind to this list
    setTimeout(() => {
        uploadManager.bindToTabTable();
    }, 0);

    return container;
}

function App() {
    const div = document.createElement('div');
    div.className = 'app-layout';
    div.appendChild(Sidebar());
    div.appendChild(ContentArea());
    div.appendChild(MediaViewer());
    div.appendChild(SelectionToolbar());
    return div;
}

function render() {
    const app = document.getElementById('app');
    app.innerHTML = '';

    if (!state.user) {
        app.appendChild(LoginScreen());
    } else {
        app.appendChild(App());

        // Force Password Change Check
        if (state.user.force_change) {
            showForceChangeModal();
        }
    }

    // Restore scroll position
    if (state.savedScrollPosition > 0) {
        setTimeout(() => {
            const contentArea = document.querySelector('.content-area');
            if (contentArea) {
                contentArea.scrollTop = state.savedScrollPosition;
                // Only clear if we are NOT in viewer mode (i.e. we fully restored the view)
                if (!state.viewerImage) {
                    state.savedScrollPosition = 0;
                }
            }
        }, 0);
    }
}

// Global Inputs
document.addEventListener('keydown', (e) => {
    if (!state.viewerImage) return;

    if (e.key === 'Escape') {
        state.viewerImage = null;
        state.zoomLevel = 100;
        state.panX = 0;
        state.panY = 0;
        render();
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        navigateViewer(1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        navigateViewer(-1);
    }
});

// Expose updateZoom to global scope for inline event handlers
window.updateZoom = updateZoom;

// Initial Render
// Initial Render
// Initial Render
checkSession();

async function checkSession() {
    try {
        const res = await fetch('/api/auth/check');
        if (res.ok) {
            const data = await res.json();
            if (data.authenticated) {
                if (data.is_admin) {
                    window.location.href = '/admin';
                    return;
                }
                state.user = { userid: data.userid, role: data.role || 'user', hosts: [], force_change: data.force_change };

                try {
                    const configRes = await fetch('/api/config');
                    if (configRes.ok) state.config = await configRes.json();
                } catch (e) {
                    state.config = { port: 8877, ai: "YES", search: "YES", people: "YES", discover: "YES" };
                }

                // Fetch data in background
                scanFiles();
                await fetchDashboardStats();
                const media = state.dashboardStats?.media || {};
                const hasMedia = (media.photos > 0) || (media.videos > 0) || (media.albums > 0) || (media.screenshots > 0);
                state.view = hasMedia ? 'photos' : 'upload';
                render(); // Render immediately

                refreshViewData();
            }
        }
    } catch (e) { console.error("Session check failed", e); }
    // If not authenticated, render will show login screen (default)
    if (!state.user) render();
}



function showForceChangeModal() {
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    // Prevent closing by clicking background

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Change Password Required</div>
            <!-- No close button -->
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:12px;">
            <p style="color:var(--text-secondary); font-size:0.9rem;">To secure your account, you must change your password before continuing.</p>
            <input type="password" id="forceNewPass" placeholder="New Password"
                style="padding:10px 14px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.9rem;">
            <input type="password" id="forceConfirmPass" placeholder="Confirm New Password"
                style="padding:10px 14px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.9rem;">
        </div>
        <div class="modal-footer">
            <button id="forceChangeBtn" style="
                background: linear-gradient(135deg, #6c8cff 0%, #a78bfa 100%);
                color: #fff; border: none; border-radius: 8px; padding: 10px 24px;
                font-size: 0.9rem; font-weight: 600; cursor: pointer; width: 100%;
            ">Update Password</button>
            <button id="logoutBtn" style="
                background: transparent; border: 1px solid #444; margin-top: 10px;
                color: #aaa; border-radius: 8px; padding: 8px 24px;
                font-size: 0.8rem; cursor: pointer; width: 100%;
            ">Logout</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Logout handler
    modal.querySelector('#logoutBtn').onclick = async () => {
        await fetch('/api/logout', { method: 'POST' });
        window.location.reload();
    };

    // Update handler
    modal.querySelector('#forceChangeBtn').onclick = async () => {
        const p1 = modal.querySelector('#forceNewPass').value;
        const p2 = modal.querySelector('#forceConfirmPass').value;

        if (!p1 || !p2) { alert('Please enter both fields'); return; }
        if (p1 !== p2) { alert('Passwords do not match'); return; }

        try {
            const res = await fetch('/api/auth/change_password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_password: p1 })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                alert('Password updated successfully!');
                overlay.remove();
                state.user.force_change = false; // clear flag locally
            } else {
                alert(data.error || 'Failed to update password');
            }
        } catch (e) {
            alert('Network error');
        }
    };
}
