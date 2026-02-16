const state = {
    user: null, // { userid: '...' }
    view: localStorage.getItem('activeView') || 'dashboard', // 'dashboard' | 'files' | 'photos' | 'screenshots' | 'videos' | 'people' | 'search' | 'discover' | 'albums'
    dashboardStats: null,


    // File Explorer State
    currentPath: '', // Relative path from user root
    explorerItems: [], // Items in currentPath
    fileViewMode: 'list', // 'list' | 'grid'
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

    // Discover State
    memories: [], // [{type, title, description, photos}]

    // Sharing State
    sharedPhotos: {}, // { ownerEmail: [photos] }

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
            state.view = data.role === 'guest' ? 'photos' : 'dashboard';
            render(); // Render immediately

            // Initialize views in background
            scanFiles();
            fetchDashboardStats();
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
            body: JSON.stringify({ userid: state.user.userid })
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
        const url = `/api/files/list?userid=${encodeURIComponent(state.user.userid)}&path=${encodeURIComponent(path)}`;
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

function addShareOverlay(item, photo) {
    // Guests cannot share
    if (state.user && state.user.role === 'guest') return;
    if (photo.is_received) {
        // Received photo: show "Shared" badge always + unshare btn on hover
        const badge = document.createElement('div');
        badge.className = 'shared-badge';
        badge.innerHTML = `<i class="fa-solid fa-share-nodes"></i> Shared`;
        item.appendChild(badge);

        const unshareBtn = document.createElement('button');
        unshareBtn.className = 'unshare-btn';
        unshareBtn.title = 'Remove shared photo';
        unshareBtn.innerHTML = '<i class="fa-solid fa-xmark"></i>';
        unshareBtn.onclick = (e) => {
            e.stopPropagation();
            if (confirm('Remove this shared photo from your library?')) {
                unsharePhoto(photo.id, photo.shared_by);
            }
        };
        item.appendChild(unshareBtn);
        // No share button for received photos
    } else {
        // Owned photo: show share button
        const shareBtn = document.createElement('button');
        shareBtn.className = 'share-btn';
        shareBtn.title = 'Share';
        shareBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i>';
        shareBtn.onclick = (e) => {
            e.stopPropagation();
            openShareModal(photo.id, photo.shared_with || []);
        };
        item.appendChild(shareBtn);

        // If already shared, show indicator
        if (photo.shared_with && photo.shared_with.length > 0) {
            const indicator = document.createElement('div');
            indicator.className = 'shared-indicator';
            indicator.innerHTML = `<i class="fa-solid fa-share-nodes"></i> ${photo.shared_with.length}`;
            item.appendChild(indicator);
        }
    }
}

function openShareModal(photoId, alreadySharedWith) {
    // Remove existing modal if any
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';

    const modal = document.createElement('div');
    modal.className = 'modal';

    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Share Photo</div>
            <button class="modal-close"><i class="fa-solid fa-times"></i></button>
        </div>
        <div class="modal-body">
            <div class="user-list" id="shareUserList">
                <div style="text-align:center; color:var(--text-secondary); padding:10px;">Loading users...</div>
            </div>
            <button class="share-submit-btn" id="shareSubmitBtn" disabled>Share</button>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };

    const selectedUsers = new Set();

    // Load users
    const loadUsers = async () => {
        const listContainer = modal.querySelector('#shareUserList');
        try {
            const res = await fetch('/api/users/list');
            const data = await res.json();
            listContainer.innerHTML = '';

            if (!data.users || data.users.length === 0) {
                listContainer.innerHTML = `<div style="text-align:center; color:var(--text-secondary); padding:10px;">No other users found.</div>`;
                return;
            }

            data.users.forEach(user => {
                const isAlreadyShared = alreadySharedWith.includes(user.email);
                const item = document.createElement('div');
                item.className = 'user-list-item' + (isAlreadyShared ? ' already-shared' : '');

                const initial = user.email.charAt(0).toUpperCase();

                item.innerHTML = `
                    <div class="user-avatar"><i class="fa-solid fa-user"></i></div>
                    <div class="user-email">${user.email}${user.type === 'guest' ? ' <span style="color:#a78bfa;font-size:11px;">(Guest)</span>' : ''}${isAlreadyShared ? ' <span style="color:#34c759;font-size:11px;">(already shared)</span>' : ''}</div>
                    <div class="user-check"><i class="fa-solid fa-check"></i></div>
                `;

                if (!isAlreadyShared) {
                    item.onclick = () => {
                        if (selectedUsers.has(user.email)) {
                            selectedUsers.delete(user.email);
                            item.classList.remove('selected');
                        } else {
                            selectedUsers.add(user.email);
                            item.classList.add('selected');
                        }
                        const btn = modal.querySelector('#shareSubmitBtn');
                        btn.disabled = selectedUsers.size === 0;
                        btn.textContent = selectedUsers.size > 0 ? `Share with ${selectedUsers.size} user${selectedUsers.size > 1 ? 's' : ''}` : 'Share';
                    };
                } else {
                    item.style.opacity = '0.5';
                    item.style.cursor = 'default';
                }

                listContainer.appendChild(item);
            });
        } catch (e) {
            console.error(e);
            listContainer.innerHTML = `<div style="color:#ff3b30; padding:10px;">Failed to load users.</div>`;
        }
    };

    loadUsers();

    // Submit handler
    const submitBtn = modal.querySelector('#shareSubmitBtn');
    submitBtn.onclick = async () => {
        if (selectedUsers.size === 0) return;
        submitBtn.disabled = true;
        submitBtn.textContent = 'Sharing...';

        try {
            const res = await fetch('/api/share', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_id: photoId, recipients: Array.from(selectedUsers) })
            });
            const data = await res.json();
            if (data.success) {
                close();
                // Refresh current view
                if (state.view === 'photos' || state.view === 'screenshots' || state.view === 'videos') {
                    fetchTimeline();
                }
                render();
            } else {
                alert(data.error || 'Share failed');
                submitBtn.disabled = false;
                submitBtn.textContent = 'Share';
            }
        } catch (e) {
            console.error(e);
            alert('Error sharing photo');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Share';
        }
    };
}

async function unsharePhoto(photoId, ownerEmail) {
    try {
        const res = await fetch('/api/unshare/received', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ photo_id: photoId })
        });
        if (res.ok) {
            // Refresh
            if (state.view === 'photos' || state.view === 'screenshots' || state.view === 'videos') {
                fetchTimeline();
            }
            render();
        } else {
            const data = await res.json();
            alert(data.error || 'Unshare failed');
        }
    } catch (e) {
        console.error(e);
        alert('Error unsharing photo');
    }
}

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
        let url = `/api/timeline?userid=${encodeURIComponent(state.user.userid)}`;
        if (type) {
            url += `&type=${encodeURIComponent(type)}`;
        }
        if (state.photoSearchQuery) {
            url += `&search=${encodeURIComponent(state.photoSearchQuery)}`;
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

async function fetchAlbumPhotos(albumId) {
    state.loading = true;
    render();
    try {
        const url = `/api/albums/${albumId}/photos?userid=${encodeURIComponent(state.user.userid)}`;
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok && data.photos) {
            // Find album metadata from state.albums
            const albumMeta = state.albums.find(a => a.id === albumId);
            state.currentAlbum = {
                id: albumId,
                photos: data.photos,
                album_type: albumMeta ? albumMeta.album_type : 'manual'
            };
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
        const url = `/api/discover/memories?userid=${encodeURIComponent(state.user.userid)}`;
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
        const res = await fetch(`/api/dashboard/stats?userid=${encodeURIComponent(state.user.userid)}`);
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
    else if (view === 'files') fetchExplorer(state.currentPath || '');
    else if (view === 'dashboard') fetchDashboardStats();
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
            body: JSON.stringify({ userid: state.user.userid, path: fullPath, new_name: newName })
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
            body: JSON.stringify({ userid: state.user.userid, path: fullPath })
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
            body: JSON.stringify({ userid: state.user.userid, paths })
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
            body: JSON.stringify({ userid: state.user.userid, paths })
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
        if (state.albums.length === 0) {
            listContainer.innerHTML = `<div style="text-align:center; color:var(--text-secondary); padding:10px;">No albums found.</div>`;
        }

        state.albums.forEach(album => {
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
    el.innerHTML = `<div class="sidebar-title">Library</div>`;

    const allItems = [
        { id: 'dashboard', icon: 'fa-gauge-high', label: 'Dashboard' },
        { id: 'files', icon: 'fa-folder', label: 'Files' },
        { id: 'photos', icon: 'fa-images', label: 'Photos' },
        { id: 'screenshots', icon: 'fa-mobile-screen', label: 'Screenshots' },
        { id: 'videos', icon: 'fa-video', label: 'Videos' },
        { id: 'albums', icon: 'fa-book', label: 'Albums' },
        { id: 'shared', icon: 'fa-share-nodes', label: 'Shared with me' },
        { id: 'people', icon: 'fa-users', label: 'People' },
        { id: 'discover', icon: 'fa-sparkles', label: 'Discover' }
    ];

    let items = allItems;
    // Guests: only show photos, screenshots, videos, albums
    if (state.user && state.user.role === 'guest') {
        const guestTabs = ['photos', 'screenshots', 'videos', 'albums'];
        items = allItems.filter(i => guestTabs.includes(i.id));
    }

    items.forEach(item => {
        const nav = document.createElement('div');
        nav.className = `nav-item ${state.view === item.id ? 'active' : ''}`;
        nav.innerHTML = `<i class="fa-solid ${item.icon}"></i> ${item.label}`;
        nav.onclick = () => {
            state.view = item.id;
            localStorage.setItem('activeView', item.id);
            state.photoSearchQuery = '';

            if (item.id === 'albums') {
                state.currentAlbum = null; // Reset album detail view
            }
            if (item.id === 'people') {
                state.currentPerson = null; // Reset person detail view
                state.personPhotos = [];
            }

            refreshViewData();
            render();
        };
        el.appendChild(nav);
    });

    // Logout Button
    const logoutBtn = document.createElement('div');
    logoutBtn.className = 'nav-item logout-btn'; // Add a class for specific styling
    logoutBtn.innerHTML = `<i class="fa-solid fa-sign-out-alt"></i> Logout`;
    logoutBtn.onclick = logout;

    // Add spacer or style to push to bottom if flex container
    // Assuming sidebar is flex column, we can use margin-top: auto on this button
    logoutBtn.style.marginTop = 'auto'; // Push to bottom
    el.appendChild(logoutBtn);

    return el;
}

async function logout() {
    if (!confirm('Are you sure you want to log out?')) return;
    try {
        await fetch('/api/logout', { method: 'POST' });
        window.location.reload(); // Reload to clear state and show login
    } catch (e) {
        console.error(e);
        window.location.reload();
    }
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
        addShareOverlay(item, photo);

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

    // People Select
    const peopleSelect = document.createElement('div');
    peopleSelect.className = 'search-people-select';
    peopleSelect.innerHTML = `<h4>With People:</h4>`;
    const peopleList = document.createElement('div');
    peopleList.className = 'people-chips';

    state.people.forEach(p => {
        const chip = document.createElement('div');
        chip.className = `person-chip ${state.searchPersonIds.includes(p.id) ? 'selected' : ''}`;
        chip.innerText = p.name;
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

    // Text Search
    const searchBox = document.createElement('div');
    searchBox.className = 'search-box-row';
    searchBox.innerHTML = `
        <input type="text" id="searchInput" placeholder="Search by description..." value="${state.searchQuery}">
        <button id="doSearchBtn">Search</button>
    `;
    controls.appendChild(searchBox);

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
            addShareOverlay(item, r);

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
            fetchExplorer(parts.join('/'));
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
                        const isVideo = lowerName.match(/\.(mp4|mov|avi|mkv|webm)$/);
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
                    const isVideo = item.name.toLowerCase().match(/\.(mp4|mov|avi|mkv|webm)$/);
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

// Helper to determine if a file explorer path matches the server's image serving structure
function parseImagePath(dirPath, filename) {
    // Server serves: /resource/image/<userid>/<device>/<filename>
    // Where valid internal path is: user_dir/<device>/files/<filename>

    // Check if dirPath starts with "something/files"
    const parts = dirPath.split('/');
    if (parts.length >= 2 && parts[1] === 'files') {
        const device = parts[0];
        // The "filename" arg in server route is <path:filename>, so it includes subdirs if any
        // If dirPath is "myiphone/files/subdir", rest is "subdir"
        const relativeSubdir = parts.slice(2).join('/');
        const finalPath = relativeSubdir ? `${relativeSubdir}/${filename}` : filename;

        // original URL for img src
        const url = `/resource/image/${state.user.userid}/${device}/${finalPath}`;

        // Construct Real Path relative to user root for metadata API
        // This must match database path structure: <device>/files/<subdir>/<filename>
        const realPath = `${device}/files/${finalPath}`;

        return {
            url: url,
            path: realPath
        };
    }
    return null; // Not viewable via the standard image route
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
            addShareOverlay(item, photo);

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
                // Save scroll position before opening viewer
                const contentArea = document.querySelector('.content-area');
                if (contentArea) {
                    state.savedScrollPosition = contentArea.scrollTop;
                }

                // Flatten timeline groups for viewer list
                const allPhotos = state.timelineGroups.flatMap(g => g.photos);
                state.viewerList = allPhotos;
                state.viewerIndex = allPhotos.findIndex(p => p.id === photo.id);

                state.viewerImage = { src: photo.image_url, type: photo.type };
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
            addShareOverlay(item, photo);

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

            // Share button (for manual albums that don't contain shared photos)
            if (album.album_type === 'manual' && !album.has_shared_photos) {
                const shareBtn = document.createElement('button');
                shareBtn.className = 'album-delete-btn'; // reuse styling
                shareBtn.style.right = '44px';
                shareBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i>';
                shareBtn.title = 'Share Album';
                shareBtn.onclick = (e) => {
                    e.stopPropagation();
                    openAlbumShareModal(album);
                };
                card.appendChild(shareBtn);
            }

            // Click to view album
            card.onclick = () => {
                fetchAlbumPhotos(album.id);
            };

            grid.appendChild(card);
        });
    }

    container.appendChild(grid);
    return container;
}

function openAlbumShareModal(album) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.style.width = '500px';

    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Share Album: ${album.name}</div>
            <button class="modal-close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body">
            <p style="font-size:13px; color:var(--text-secondary); margin-bottom:10px;">Share a full copy of this album with another user.</p>
            <div class="user-list" id="albumShareUserList">
                <div style="text-align:center; padding:20px; color:var(--text-secondary);">Loading users...</div>
            </div>
            <button class="share-submit-btn" id="albumShareSubmitBtn" disabled>Share Album</button>
        </div>
    `;

    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };

    // Load users list (same pattern as photo sharing)
    const selectedUsers = new Set();
    fetch('/api/users/list')
        .then(r => r.json())
        .then(data => {
            const listContainer = modal.querySelector('#albumShareUserList');
            listContainer.innerHTML = '';

            if (!data.users || data.users.length === 0) {
                listContainer.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-secondary);">No other users found.</div>';
                return;
            }

            data.users.forEach(user => {
                const item = document.createElement('div');
                item.className = 'user-list-item';
                item.innerHTML = `
                    <div class="user-avatar">${user.email[0].toUpperCase()}</div>
                    <div class="user-email">${user.email}</div>
                    <div class="user-check"><i class="fa-solid fa-check"></i></div>
                `;

                item.onclick = () => {
                    if (selectedUsers.has(user.email)) {
                        selectedUsers.delete(user.email);
                        item.classList.remove('selected');
                    } else {
                        selectedUsers.add(user.email);
                        item.classList.add('selected');
                    }

                    const btn = modal.querySelector('#albumShareSubmitBtn');
                    btn.disabled = selectedUsers.size === 0;
                    btn.textContent = selectedUsers.size > 0 ? `Share with ${selectedUsers.size} user${selectedUsers.size > 1 ? 's' : ''}` : 'Share Album';
                };

                listContainer.appendChild(item);
            });
        })
        .catch(err => {
            console.error(err);
            modal.querySelector('#albumShareUserList').innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-secondary);">Error loading users.</div>';
        });

    // Share submit
    const submitBtn = modal.querySelector('#albumShareSubmitBtn');
    submitBtn.onclick = async () => {
        if (selectedUsers.size === 0) return;
        submitBtn.disabled = true;
        submitBtn.textContent = 'Sharing...';

        let successCount = 0;
        let errorMsg = '';

        for (const email of selectedUsers) {
            try {
                const res = await fetch(`/api/albums/${album.id}/share/user`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const d = await res.json();
                if (res.ok) {
                    successCount++;
                } else {
                    errorMsg = d.error || 'Share failed';
                }
            } catch (e) {
                console.error(e);
                errorMsg = 'Error sharing album';
            }
        }

        if (successCount > 0) {
            alert(`Album shared with ${successCount} user${successCount > 1 ? 's' : ''} successfully!`);
            close();
        } else {
            alert(errorMsg || 'Share failed');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Share Album';
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
                addShareOverlay(item, photo);
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
        let url = `/api/photo/metadata?userid=${encodeURIComponent(state.user.userid)}`;

        // Prefer ID if available
        if (image.id) {
            url += `&id=${encodeURIComponent(image.id)}`;
        } else if (image.path) {
            // Explicit path provided (e.g. from FileExplorer)
            url += `&path=${encodeURIComponent(image.path)}`;
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
        const res = await fetch(`/api/received-photos?userid=${encodeURIComponent(state.user.userid)}`);
        const data = await res.json();
        if (res.ok && data.shared_photos) {
            state.sharedPhotos = data.shared_photos;
        }
    } catch (e) {
        console.error('Shared photos fetch error', e);
    } finally {
        state.loading = false;
        render();
    }
}

// --- Zoom and Pan Helpers ---

function updateViewerTransform() {
    const media = document.querySelector('.lightbox-media img, .lightbox-media video');
    if (media) {
        media.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.zoomLevel / 100})`;
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

    // Metadata Sidebar Content (Same as before)
    let metaContent = '';
    if (currentMetadata && currentMetadata.loading) {
        metaContent = '<div class="spinner"></div>';
    } else if (currentMetadata && currentMetadata.found) {
        const m = currentMetadata;
        const dateTaken = m.date_taken ? new Date(m.date_taken).toLocaleString() : 'Unknown';
        const dateUploaded = m.timestamp ? new Date(m.timestamp).toLocaleString() : 'Unknown';

        metaContent = `
            <div class="meta-title">${m.filename}</div>
            
            <div class="meta-row">
                <div class="meta-label">Date Taken</div>
                <div class="meta-value">${dateTaken}</div>
            </div>
            
            <div class="meta-row">
                <div class="meta-label">Date Uploaded</div>
                <div class="meta-value">${dateUploaded}</div>
            </div>
            
            ${m.size ? `
            <div class="meta-row">
                <div class="meta-label">Size</div>
                <div class="meta-value">${formatSize(m.size)}</div>
            </div>` : ''}
            
            ${(m.location_lat && m.location_lon) ? `
            <div class="meta-row">
                <div class="meta-label">GPS Location</div>
                <div class="meta-value">
                    <a href="https://www.google.com/maps/search/?api=1&query=${m.location_lat},${m.location_lon}" target="_blank" style="color:var(--accent-color)">
                        ${m.location_lat.toFixed(4)}, ${m.location_lon.toFixed(4)}
                    </a>
                </div>
            </div>` : ''}
             
            ${m.description ? `
            <div class="meta-row">
                <div class="meta-label">Description</div>
                <div class="meta-value">${m.description}</div>
            </div>` : ''}

            <!-- Zoom Control -->
            <div class="meta-row" style="margin-top:20px; border-top:1px solid rgba(255,255,255,0.1); padding-top:10px;">
                <div class="meta-label">Zoom</div>
                <div class="meta-value" style="display:flex; align-items:center; gap:10px;">
                    <i class="fa-solid fa-magnifying-glass-minus" style="font-size:12px; color:var(--text-secondary)"></i>
                    <input type="range" min="20" max="500" value="${state.zoomLevel || 100}" class="zoom-slider" style="flex:1" oninput="updateZoomUI(this.value)">
                    <i class="fa-solid fa-magnifying-glass-plus" style="font-size:12px; color:var(--text-secondary)"></i>
                    <span id="zoomValue" style="min-width:40px; text-align:right; font-size:12px; color:var(--text-secondary)">${state.zoomLevel || 100}%</span>
                </div>
            </div>
        `;
    } else {
        metaContent = `<div style="color:var(--text-secondary); padding-top:20px; text-align:center;">No metadata available</div>`;
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
        <div class="lightbox-content">
            <div class="lightbox-media" id="mediaContainer">
                 ${mediaContent}
                 ${navControls}
            </div>
            <div class="lightbox-sidebar" onclick="event.stopPropagation()">
                ${metaContent}
            </div>
        </div>
        <div class="close-help">Press ESC to close</div>
    `;

    // Event Listeners for Interaction
    setTimeout(() => {
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
                img.ondragstart = (e) => e.preventDefault();
                img.style.cursor = 'grab';
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
        }
    }, 0);

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

    // 5. Guest Management Card (users only, after system health)
    if (!state.user || state.user.role !== 'guest') {
        const guestCard = document.createElement('div');
        guestCard.className = 'dash-card wide';
        guestCard.innerHTML = `
            <div class="card-header"><i class="fa-solid fa-user-plus"></i> Guest Management</div>
            <div id="guestCardBody" style="min-height: 60px;">
                <div style="text-align:center; color:var(--text-secondary); padding:10px;">Loading guests...</div>
            </div>
            <div style="margin-top: 14px;">
                <button id="inviteGuestBtn" style="
                    background: linear-gradient(135deg, #6c8cff 0%, #a78bfa 100%);
                    color: #fff; border: none; border-radius: 8px; padding: 10px 20px;
                    font-size: 0.9rem; font-weight: 600; cursor: pointer;
                    transition: opacity 0.2s;
                " onmouseover="this.style.opacity='0.85'" onmouseout="this.style.opacity='1'"
                ><i class="fa-solid fa-plus"></i> Invite Guest</button>
            </div>
        `;
        grid.appendChild(guestCard);

        // Load guest list asynchronously
        setTimeout(() => loadGuestList(guestCard.querySelector('#guestCardBody')), 0);
        guestCard.querySelector('#inviteGuestBtn').onclick = () => openInviteGuestModal();
    }

    container.appendChild(grid);



    return container;
}

function SharedView() {
    const container = document.createElement('div');
    container.className = 'shared-view';

    const owners = Object.keys(state.sharedPhotos);

    if (owners.length === 0) {
        container.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">No photos shared with you yet.</div>`;
        return container;
    }

    owners.forEach(owner => {
        const section = document.createElement('div');
        section.style.marginBottom = '24px';

        const header = document.createElement('h3');
        header.style.color = 'var(--text-secondary)';
        header.style.marginBottom = '12px';
        header.innerHTML = `<i class="fa-solid fa-user-tag"></i> Shared by ${owner}`;
        section.appendChild(header);

        const grid = document.createElement('div');
        grid.className = 'photo-grid';

        state.sharedPhotos[owner].forEach(photo => {
            const item = document.createElement('div');
            item.className = 'photo-item';

            const img = document.createElement('img');
            img.src = photo.thumbnail_url;
            img.loading = "lazy";
            img.onload = () => img.classList.add('loaded');
            item.appendChild(img);

            item.onclick = () => {
                state.viewerList = state.sharedPhotos[owner];
                state.viewerIndex = state.sharedPhotos[owner].findIndex(p => p.id === photo.id);
                state.viewerImage = { src: photo.image_url, type: photo.type };
                render();
            };

            grid.appendChild(item);
        });

        section.appendChild(grid);
        container.appendChild(section);
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
    } else {
        el.innerHTML = `<div style="padding:20px; color:var(--text-secondary)">Coming soon...</div>`;
    }

    return el;
}

function App() {
    const div = document.createElement('div');
    div.className = 'app-layout';
    div.appendChild(Sidebar());
    div.appendChild(ContentArea());
    div.appendChild(MediaViewer());
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
    if (e.key === 'Escape' && state.viewerImage) {
        state.viewerImage = null;
        state.zoomLevel = 100;
        state.panX = 0;
        state.panY = 0;
        render();
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
                render(); // Render immediately

                // Fetch data in background
                scanFiles();
                refreshViewData();
            }
        }
    } catch (e) { console.error("Session check failed", e); }
    // If not authenticated, render will show login screen (default)
    if (!state.user) render();
}

// ================================
// Guest Management Frontend
// ================================

async function loadGuestList(container) {
    try {
        const res = await fetch(`/api/guests/list?userid=${encodeURIComponent(state.user.userid)}`);
        const data = await res.json();
        if (!data.guests || data.guests.length === 0) {
            container.innerHTML = '<div style="text-align:center; color:var(--text-secondary); padding: 12px;">No guests invited yet.</div>';
            return;
        }
        container.innerHTML = '';
        const table = document.createElement('div');
        table.style.cssText = 'display:flex; flex-direction:column; gap:8px;';
        data.guests.forEach(g => {
            const statusColor = g.status === 'active' ? '#4ade80' : g.status === 'expired' ? '#fbbf24' : '#f87171';
            const row = document.createElement('div');
            row.style.cssText = 'display:flex; align-items:center; gap:12px; padding:10px 14px; background:var(--surface-2, #1e2030); border-radius:8px;';
            row.innerHTML = `
                <div style="flex:1; min-width:0;">
                    <div style="font-weight:600; font-size:0.9rem;">${g.email}</div>
                    <div style="font-size:0.75rem; color:var(--text-secondary, #888);">Added: ${g.added_date ? g.added_date.split('T')[0] : '\u2014'} \u00b7 Expires: ${g.access_till ? g.access_till.split('T')[0] : '\u2014'}</div>
                </div>
                <span style="padding:3px 10px; border-radius:12px; font-size:0.7rem; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; background:${statusColor}22; color:${statusColor};">${g.status}</span>
                <div class="guest-actions" style="display:flex; gap:6px;"></div>
            `;
            const actions = row.querySelector('.guest-actions');
            if (g.status === 'active') {
                actions.appendChild(makeGuestBtn('Revoke', '#f87171', () => guestAction('revoke', g.email)));
            } else {
                actions.appendChild(makeGuestBtn('Reactivate', '#4ade80', () => openReactivateModal(g.email)));
            }
            actions.appendChild(makeGuestBtn('Delete', '#ef4444', () => guestAction('delete', g.email)));
            table.appendChild(row);
        });
        container.appendChild(table);
    } catch (e) {
        container.innerHTML = '<div style="color:#f87171; padding:10px;">Error loading guests.</div>';
    }
}

function makeGuestBtn(label, color, onclick) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.style.cssText = `background:${color}22; color:${color}; border:1px solid ${color}44; border-radius:6px; padding:4px 10px; font-size:0.75rem; font-weight:600; cursor:pointer;`;
    btn.onclick = onclick;
    return btn;
}

async function guestAction(action, email, extraBody = {}) {
    if (action === 'delete' && !confirm(`Remove guest ${email}?`)) return;
    if (action === 'revoke' && !confirm(`Revoke access for ${email}?`)) return;
    try {
        const res = await fetch(`/api/guests/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userid: state.user.userid, email, ...extraBody })
        });
        const data = await res.json();
        if (data.success) {
            fetchDashboardStats();
            render();
        } else {
            alert(data.error || 'Action failed');
        }
    } catch (e) {
        alert('Network error');
    }
}

function openInviteGuestModal() {
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Invite Guest</div>
            <button class="modal-close"><i class="fa-solid fa-times"></i></button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:12px;">
            <input type="email" id="guestEmail" placeholder="Guest email address"
                style="padding:10px 14px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.9rem;">
            <input type="password" id="guestPassword" placeholder="Set a password for the guest"
                style="padding:10px 14px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.9rem;">
            <div style="display:flex; align-items:center; gap:10px;">
                <label style="font-size:0.85rem; color:var(--text-secondary, #888);">Access Duration:</label>
                <select id="guestDuration" style="padding:8px 12px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.85rem;">
                    <option value="7">7 days</option>
                    <option value="14">14 days</option>
                    <option value="30" selected>30 days</option>
                    <option value="60">60 days</option>
                    <option value="90">90 days</option>
                    <option value="180">6 months</option>
                    <option value="365">1 year</option>
                </select>
            </div>
        </div>
        <div class="modal-footer">
            <button id="inviteSubmitBtn" style="
                background: linear-gradient(135deg, #6c8cff 0%, #a78bfa 100%);
                color: #fff; border: none; border-radius: 8px; padding: 10px 24px;
                font-size: 0.9rem; font-weight: 600; cursor: pointer; width: 100%;
            ">Send Invitation</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };

    modal.querySelector('#inviteSubmitBtn').onclick = async () => {
        const email = modal.querySelector('#guestEmail').value.trim();
        const password = modal.querySelector('#guestPassword').value;
        const duration = modal.querySelector('#guestDuration').value;
        if (!email || !password) { alert('Email and password are required'); return; }
        try {
            const res = await fetch('/api/guests/invite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userid: state.user.userid, email, password, duration_days: parseInt(duration) })
            });
            const data = await res.json();
            if (data.success) {
                close();
                fetchDashboardStats();
                render();
            } else {
                alert(data.error || 'Failed to invite guest');
            }
        } catch (e) {
            alert('Network error');
        }
    };
}

function openReactivateModal(email) {
    const existing = document.querySelector('.modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-header">
            <div class="modal-title">Reactivate Guest: ${email}</div>
            <button class="modal-close"><i class="fa-solid fa-times"></i></button>
        </div>
        <div class="modal-body" style="display:flex; flex-direction:column; gap:12px;">
            <div style="display:flex; align-items:center; gap:10px;">
                <label style="font-size:0.85rem; color:var(--text-secondary, #888);">New Duration:</label>
                <select id="reactivateDuration" style="padding:8px 12px; border-radius:8px; border:1px solid var(--border-color, #333); background:var(--surface-2, #1e2030); color:var(--text-primary, #eee); font-size:0.85rem;">
                    <option value="7">7 days</option>
                    <option value="14">14 days</option>
                    <option value="30" selected>30 days</option>
                    <option value="60">60 days</option>
                    <option value="90">90 days</option>
                    <option value="180">6 months</option>
                    <option value="365">1 year</option>
                </select>
            </div>
        </div>
        <div class="modal-footer">
            <button id="reactivateSubmitBtn" style="
                background: linear-gradient(135deg, #4ade80 0%, #22c55e 100%);
                color: #fff; border: none; border-radius: 8px; padding: 10px 24px;
                font-size: 0.9rem; font-weight: 600; cursor: pointer; width: 100%;
            ">Reactivate</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector('.modal-close').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };

    modal.querySelector('#reactivateSubmitBtn').onclick = async () => {
        const duration = modal.querySelector('#reactivateDuration').value;
        await guestAction('reactivate', email, { duration_days: parseInt(duration) });
        close();
    };
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
