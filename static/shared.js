// Frontend logic for viewing a shared link

// XSS prevention: escape user-controlled strings before HTML insertion
function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

const linkHash = window.location.pathname.split('/s/')[1];
let verifiedPassword = null; // Stored after successful verify for immediate reuse

document.addEventListener('DOMContentLoaded', () => {
    loadSharedContent();
});

async function loadSharedContent() {
    showLoading();
    try {
        let url = `/api/links/view?link_hash=${linkHash}`;
        if (verifiedPassword) {
            url += `&password=${encodeURIComponent(verifiedPassword)}`;
        }
        const res = await fetch(url);
        const data = await res.json();

        if (res.ok) {
            renderContent(data);

            // Setup download ZIP button link route
            const downloadBtn = document.getElementById('download-zip-btn');
            if (downloadBtn) {
                downloadBtn.href = `/api/links/download_zip?link_hash=${linkHash}`;
            }
        } else if (data.requires_password) {
            renderPasswordPrompt(verifiedPassword ? (data.error || 'Incorrect password') : null);
            if (verifiedPassword) verifiedPassword = null; // Clear bad password
        } else {
            renderError(data.error || 'Failed to load link');
        }
    } catch (e) {
        renderError('Failed to connect to server');
    }
}

function showLoading() {
    document.getElementById('loading-state').style.display = 'flex';
    document.getElementById('shared-content').style.display = 'none';
}

function renderError(msg) {
    document.getElementById('loading-state').style.display = 'none';
    const container = document.getElementById('shared-content');
    container.style.display = 'flex';
    container.innerHTML = `
        <div style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;">
            <i class="fas fa-exclamation-triangle" style="font-size:48px; color:var(--text-secondary); margin-bottom:16px;"></i>
            <h2 style="margin-bottom:8px;">Link Unavailable</h2>
            <p style="color:var(--text-secondary);">${escapeHtml(msg)}</p>
        </div>
    `;
}

function renderPasswordPrompt(errorMsg) {
    document.getElementById('loading-state').style.display = 'none';
    document.getElementById('shared-content').style.display = 'none';

    const prompt = document.getElementById('password-prompt');
    if (prompt) prompt.style.display = 'flex';

    const errorEl = document.getElementById('password-error');
    if (errorEl) {
        if (errorMsg) {
            errorEl.textContent = errorMsg;
            errorEl.style.display = 'block';
        } else {
            errorEl.style.display = 'none';
        }
    }

    const input = document.getElementById('link-password');
    if (input) input.focus();
}

async function submitLinkPassword(e) {
    e.preventDefault();
    const password = document.getElementById('link-password').value;
    if (!password) return;

    // Store password and load content directly — view endpoint verifies inline
    verifiedPassword = password;
    loadSharedContent();
}

function renderContent(data) {
    document.getElementById('loading-state').style.display = 'none';
    const prompt = document.getElementById('password-prompt');
    if (prompt) prompt.style.display = 'none';
    document.getElementById('shared-content').style.display = 'flex';
    document.getElementById('shared-content').style.flexDirection = 'column';

    const container = document.getElementById('content-container');
    container.innerHTML = '';

    if (data.asset_type === 'photo' || data.asset_type === 'video') {
        document.getElementById('top-bar-text').textContent = 'Shared File';
        const item = data.item;

        container.innerHTML = `
            <div class="single-item-container">
                ${item.is_video
                ? `<video class="single-media" src="${item.image_url}" controls autoplay loop></video>`
                : `<img class="single-media" src="${item.image_url}" alt="Shared image">`
            }
            </div>
        `;
    } else if (data.asset_type === 'album') {
        document.getElementById('top-bar-text').textContent = data.is_multi_album ? 'Shared Albums' : 'Shared Album';

        let html = '';

        if (data.is_multi_album) {
            data.albums.forEach(album => {
                html += `
                    <div class="timeline-section">
                        <div class="timeline-date-header">
                            <div>
                                <h3>${escapeHtml(album.name)}</h3>
                                <span>${album.items.length} items • Shared by ${escapeHtml(data.owner)}</span>
                            </div>
                        </div>
                        ${album.description ? `<p style="color: var(--text-secondary); font-size: 14px; margin-top: -8px; margin-bottom: 16px; padding: 0 16px;">${escapeHtml(album.description)}</p>` : ''}
                        <div class="timeline-grid">
                `;
                album.items.forEach(item => {
                    html += `
                        <div class="photo-item" onclick="openLightbox('${item.image_url}', ${item.is_video}, ${item.id})">
                            <img src="${item.thumbnail_url}" onload="this.classList.add('loaded')" alt="Thumbnail">
                            ${item.is_video ? '<div class="play-icon-overlay"><i class="fas fa-play"></i></div>' : ''}
                        </div>
                    `;
                });
                html += `
                        </div>
                    </div>
                `;
            });
        } else {
            html += `
                <div class="timeline-section">
                    <div class="timeline-date-header">
                        <div>
                            <h3>${escapeHtml(data.album_name) || 'Shared Album'}</h3>
                            <span>${data.items.length} items • Shared by ${escapeHtml(data.owner)}</span>
                        </div>
                    </div>
                    ${data.album_description ? `<p style="color: var(--text-secondary); font-size: 14px; margin-top: -8px; margin-bottom: 16px; padding: 0 16px;">${escapeHtml(data.album_description)}</p>` : ''}
                    <div class="timeline-grid">
            `;
            data.items.forEach(item => {
                html += `
                    <div class="photo-item" onclick="openLightbox('${item.image_url}', ${item.is_video}, ${item.id})">
                        <img src="${item.thumbnail_url}" onload="this.classList.add('loaded')" alt="Thumbnail">
                        ${item.is_video ? '<div class="play-icon-overlay"><i class="fas fa-play"></i></div>' : ''}
                    </div>
                `;
            });
            html += `
                    </div>
                </div>
            `;
        }

        // Add a modern lightbox for albums
        html += `
            <div id="simple-lightbox" class="lightbox" onclick="closeLightbox(event)">
                <button class="lightbox-close-btn" onclick="closeLightbox(event, true)" title="Close">
                    <i class="fa-solid fa-xmark"></i> Close
                </button>
                <div class="lightbox-actions" style="position: absolute; top: 24px; right: 24px; z-index: 9999;">
                    <button class="lightbox-delete-btn" onclick="hidePhoto(event)" title="Delete from Shared View">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </div>
                <div class="lightbox-content">
                    <div class="lightbox-media" id="lightbox-media-container"></div>
                </div>
            </div>
        `;

        container.innerHTML = html;
    }
}

// Simple lightbox functions for global scope
let currentLightboxPhotoId = null;

window.openLightbox = function (url, isVideo, photoId) {
    const lb = document.getElementById('simple-lightbox');
    const mediaContainer = document.getElementById('lightbox-media-container');
    currentLightboxPhotoId = photoId;

    if (isVideo) {
        mediaContainer.innerHTML = `<video src="${url}" controls autoplay style="max-width:100%; max-height:100%;"></video>`;
    } else {
        mediaContainer.innerHTML = `<img src="${url}" style="max-width:100%; max-height:100%; border-radius:4px;">`;
    }

    lb.classList.add('active');
};

window.closeLightbox = function (e, force = false) {
    if (force || (e && (e.target.id === 'simple-lightbox' || e.target.closest('.lightbox-content') === null))) {
        // Only close if we didn't click the delete button container
        if (e && e.target.closest('.lightbox-actions')) return;

        const lb = document.getElementById('simple-lightbox');
        if (lb && lb.classList.contains('active')) {
            lb.classList.remove('active');
            document.getElementById('lightbox-media-container').innerHTML = '';
            currentLightboxPhotoId = null;
        }
    }
};

window.hidePhoto = async function (e) {
    if (e) {
        e.stopPropagation();
    }

    if (!currentLightboxPhotoId) return;

    const pwd = prompt("Enter the shared link password to delete this photo from the view:");
    if (pwd === null) return; // Cancelled
    if (!pwd) {
        alert("Password is required to delete photos.");
        return;
    }

    try {
        const res = await fetch('/api/links/hide', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                link_hash: linkHash,
                photo_id: currentLightboxPhotoId,
                password: pwd
            })
        });

        const data = await res.json();
        if (res.ok) {
            window.closeLightbox(null, true);
            // Reload the album view to reflect the deletion
            loadSharedContent();
        } else {
            alert(data.error || "Failed to hide photo.");
        }
    } catch (err) {
        alert("Error connecting to server.");
    }
};

// Global escape key handler to close simple lightbox
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        window.closeLightbox(null, true);
    }
});
